#!/usr/bin/env python3.11

import pandas as panda
import click
import subprocess
import re
import shutil
import os
import fileinput
import yaml


@click.group()
def main():
    return True


@main.command('list-channels', short_help="Lists operator channel versions")
@click.option('--version', help='List the CP4I operator channels')
def channel_handler(version):
    return _channel_handler(version, False)


def _channel_handler(version):

    url = f'https://www.ibm.com/docs/en/cloud-paks/cp-integration/{version}?topic=reference-operator-channel-versions-this-release'

    click.secho(f"\nOperator Channels for {version} ({url})", fg="green")
    click.secho("----------------------------------------------------------------------------------------------------------------------------------------", fg="green")
    try:
        tables = panda.read_html(url, match="Capability name")
    except Exception as e:
        click.secho(f"\nIs version {version} valid?, {e}\n", fg="red")
        return None

    table = tables[0]
    channels = {}

    for i in range(0, len(table.index)-1):
        operator_friendly_name = table.iloc[i, 1]
        if type(operator_friendly_name) is not str:
            continue
        channel = str(table.iloc[i, 2]).split(",", 1)[0]
        channels[operator_friendly_name] = channel
        # if silent is False:
        click.secho(f"{operator_friendly_name}/{table.iloc[i, 0]}: {channel}")

    click.secho("----------------------------------------------------------------------------------------------------------------------------------------", fg="green")

    return channels


@main.command('deploy-operators', short_help="Connects to CP4I IBM documentation, downloads CASE files, installs catalog sources and operators in the requested namespaces and applies the OperatorGroup resource")
@click.option('--version', help="The CP4I version, e.g. 2023.2", required=True)
@click.option('--list', is_flag=True, help="List all operators and versions")
@click.option('--catalog_source_ns', default="openshift-marketplace", help='(Experimental) The namespace to apply the catalogsources (default: openshift-marketplace)')
@click.option('--target_ns', default="openshift-operators", help='The namespace to deploy the operator subscriptons (default: openshift-operators, i.e. All Namespaces)')
@click.option('--case', '-c', multiple=True, default=["all"], help='CASE, i.e. operator, to apply (default: all)')
def operator_handler(version, catalog_source_ns, target_ns, case, list):
   
    click.clear()
    url = f'https://www.ibm.com/docs/en/cloud-paks/cp-integration/{version}?topic=images-adding-catalog-sources-cluster'

    click.secho(f"\nCASEs for {version} ({url})", fg="green")
    click.secho("---------------------------------------------------------------------------------------------------------------------------", fg="green")

    try:
        tables = panda.read_html(url, match="Export commands")
    except Exception as e:
        click.secho(f"\nIs version {version} valid?, {e}\n", fg="red")
        return None

    table = tables[0]
    command_end = " && export IBMPAK_HOME=. && oc ibm-pak get $CASE_NAME --version $CASE_VERSION && oc ibm-pak generate mirror-manifests $CASE_NAME icr.io --version $CASE_VERSION"
    cases = {}
    all_cases = {}
    for i in range(0, len(table.index)-1):
        operator_friendly_name = table.iloc[i, 0]
        command_start = table.iloc[i, 1]
        command = command_start + command_end
        operator = extract_case_name(command_start)
        operator_version = extract_case_version(command_start)
        item = [operator_friendly_name, operator_version, command]
        click.secho(
            f"{operator} ({operator_friendly_name}): \033[92m{operator_version}")
        all_cases[operator] = item
    click.secho("---------------------------------------------------------------------------------------------------------------------------", fg="green")
    click.echo()
    if list is True:
        return

    if "all" in case:
        cases = all_cases
        click.secho(
            "HINT: You can install individual operators by using -c flag mutliple times")
    else:
        for item in case:
            if item not in all_cases.keys():
                click.secho(f"{item} is not a valid CASE", fg="red")
                return
            cases[item] = all_cases.get(item)

    click.secho("Will deploy following operators: ")
    click.secho("   " + '\n   '.join(cases.keys()), fg="green")
    click.secho(f"\nCatalog sources will be applied in: ", nl=False)
    click.secho(catalog_source_ns, fg="green")
    click.secho(f"Operators will be deployed in: ", nl=False)
    click.secho(target_ns + "\n", fg="green")
    click.secho(f"Cloud Pak for Integration: ", nl=False)
    click.secho(version + "\n", fg="green")
    
    input("ENTER to continue, Ctrl-C to abort ")

    download_cases(cases)
    print("")
    oc_apply_catalog_sources(catalog_source_ns)
    if target_ns != "openshift-operators":
        click.secho(f"\nOperators will be installed in {target_ns} - OperatorGroup resource needed", fg="yellow")
        oc_apply_operator_group(target_ns)
    oc_apply_subscriptions(version, cases, catalog_source_ns, target_ns)


def oc_apply_catalog_sources(ns):

    strip_namespace()
    oc_commands = []
    if ns != "openshift-marketplace" and ns != "openshift-operators":
        oc_commands.append(f"oc new-project {ns}")
    for file in get_catalog_sources_files():
        oc_commands.append(f"oc apply -n {ns} -f {file}")

    run_commands(oc_commands)


def oc_apply_subscriptions(version, cases, source_ns, target_ns):

    click.secho("\nHandling subscriptions...", fg="green")
    channels = _channel_handler(version)
    oc_commands = []
    if target_ns != "openshift-marketplace" and target_ns != "openshift-operators":
        oc_commands.append(f"oc new-project {target_ns}")
    for operator, v in cases.items():
        operator_friendly_name = v[0]
        channel = channels.get(operator_friendly_name)
        sub = f"""apiVersion: operators.coreos.com/v1alpha1
kind: Subscription
metadata:
  name: {operator} 
spec:
  channel: {channel}
  name: {operator} 
  source: {get_catalogsource_name(operator)}
  sourceNamespace: {source_ns}
"""
        filename = "subscription-" + operator + ".yaml"
        click.secho(
            f"\nSubscription for {operator} will be written to {filename}: ", fg="green")
        click.secho(f"{sub}")
        with open(filename, 'w') as file:
            file.write(sub)
            oc_commands.append(f"oc apply -n {target_ns} -f {filename}")

    run_commands(oc_commands)


def oc_apply_operator_group(ns):
    content = f"""apiVersion: operators.coreos.com/v1
kind: OperatorGroup
metadata:
  name: ibm-integration-operatorgroup
  namespace: {ns}
spec:
  targetNamespaces:
  - {ns}
"""
    # for tns in targetns:
    #    content += f"  - {tns}\n"
    filename = "operatorgroup-" + ns + ".yaml"
    with open(filename, 'w') as file:
        file.write(content)

    click.secho(
        f"OperatorGroup for {ns} will be written to {filename}: ", fg="green")
    click.secho(f"\n{content}")

    oc_commands = []
    if ns != "openshift-marketplace" and ns != "openshift-operators":
        oc_commands.append(f"oc new-project {ns}")

    oc_commands.append(f"oc apply -n {ns} -f {filename}")
    run_commands(oc_commands)


def run_commands(oc_commands):
    click.secho("\nThe following will now run...", fg="green")
    for cmd in oc_commands:
        click.secho(f"   {cmd}", fg="yellow")

    click.secho("")
    while True:
        answer = input(
            "Press 'a' to apply the change, 'c' to continue without applying, Ctrl-C to abort: ")
        if answer == "a" or answer == "c":
            break
    if answer == "a":
        for cmd in oc_commands:
            proc = subprocess.run(cmd, shell=True)
    print("")


def get_catalogsource_name(operator):
    catalog_sources = get_catalog_sources_files()
    for yaml_file_path in catalog_sources:
        with open(yaml_file_path, 'r') as file:
            yaml_content = yaml.safe_load_all(file)

            for document in yaml_content:
                if 'metadata' in document and 'name' in document['metadata']:
                    catalog_source_name = document['metadata']['name']
                    if operator.split("-", 1)[1] in catalog_source_name:
                        return catalog_source_name


def download_cases(cases):
    if get_pak() is False:
        return
    click.secho("Downloading CASES...", fg="green")
    try:
        click.secho("Removing .ibm-pak folder...", fg="green")
        shutil.rmtree("./.ibm-pak")
    except:
        pass
    for k, v in cases.items():
        click.secho(f"\nDownloading {k}...", fg="green")
        proc = subprocess.run(v[2], shell=True)


def get_pak():
    click.secho("Checking ibm-pak is installed...", fg="green")
    proc = subprocess.run(
        ["oc", "ibm-pak"], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    if proc.returncode == 0:
        return True
    else:
        click.secho(
            "ibm-pak oc module is not installed. Get ibm-pak from https://github.com/IBM/ibm-pak#readme", fg="red")
        return False


def strip_namespace():
    try:
        file_paths = get_catalog_sources_files()
        click.secho(
            "\nStripping namespace from all catalog-sources yaml files:", fg="green")
        for file_path in file_paths:
            with fileinput.FileInput(file_path, inplace=True) as file:
                # Iterate through each line in the file
                for line in file:
                    # Remove lines starting with "namespace:" (ignoring leading spaces)
                    if not line.lstrip().startswith("namespace:"):
                        print(line, end="")
            click.secho(f"   {file_path}")
    except Exception as e:
        print(f"Error: {e}")


def extract_case_name(input_string):
    # Define the pattern for extracting CASE_NAME
    pattern = r'export CASE_NAME=([^\s]+)'

    # Use re.search to find the match
    match = re.search(pattern, input_string)

    # Check if a match is found
    if match:
        return match.group(1)
    else:
        return None


def extract_case_version(input_string):
    # Define the pattern for extracting CASE_NAME
    pattern = r'export CASE_VERSION=([^\s]+)'

    # Use re.search to find the match
    match = re.search(pattern, input_string)

    # Check if a match is found
    if match:
        return match.group(1)
    else:
        return None


def get_catalog_sources_files(directory_path=".ibm-pak", file_prefix="catalog-sources"):
    # Locate all file_name(s) under the specified directory
    file_paths = [os.path.join(root, file) for root, dirs, files in os.walk(
        directory_path) for file in files if file.startswith(file_prefix)]
    return file_paths


if __name__ == '__main__':
    main()
