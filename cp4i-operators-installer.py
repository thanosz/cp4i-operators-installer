#!/usr/bin/env python3.11

import pandas as panda
import click
import subprocess
import re
import shutil
import os
import fileinput
import yaml
import sys
import traceback 



class OperatorHandler:
    def __init__(self, version):
        self._operators = {}
        self.version = version

    
    def get_operators(self):
        return self._operators
    
    def populate(self):
        command_url = f'https://www.ibm.com/docs/en/cloud-paks/cp-integration/{self.version}?topic=images-adding-catalog-sources-cluster'
        channel_url = f'https://www.ibm.com/docs/en/cloud-paks/cp-integration/{self.version}?topic=reference-operator-channel-versions-this-release'

        operators = {}
        try:
            click.secho(f"\nConnecting to {command_url}", fg="green")
            commands_table = panda.read_html(command_url, match="Export commands")[0]
            for i in range(0, len(commands_table.index)-1):
                friendly_name = commands_table.iloc[i, 0]
                export_command = commands_table.iloc[i, 1]
                operator = Operator(friendly_name, export_command)
                operators[friendly_name] = operator

            click.secho(f"Connecting to {channel_url}", fg="green")
            channels_table = panda.read_html(channel_url, match="Capability name")[0]
            for i in range(0, len(channels_table.index)-1):
                friendly_name = channels_table.iloc[i, 1]
                if type(friendly_name) is not str:
                    continue
                channel = str(channels_table.iloc[i, 2]).split(",", 1)[0]
                operator = operators.get(friendly_name)
                if operator is not None:
                    operator.channel = channel

            for friendly_name, operator in operators.items():
                self._operators[operator.name] = operator
        except Exception as e:
            raise Exception(f"Is version {self.version} valid?, {e}")

    def filter(self, selection):
        if "all" in selection: 
            click.secho("\nHINT: You can install individual operators by using -c flag mutliple times")
            return
        
        new_operators = {}
        for name in selection:
            if name not in self._operators.keys():
                raise Exception(f"Operator '{name}' is not a valid operator name")
            new_operators[name] = self._operators.get(name)
        self._operators = new_operators

        

    
    def print(self):
        #click.clear()
        click.secho(f"\nOperators for CP4I version {self.version}", fg="green")
        click.secho("---------------------------------------------------------------------------------------------------------------------------", fg="green")
        
        for name, operator in self._operators.items():
            click.secho(f"{operator.name} ({operator.friendly_name}): version: \033[92m{operator.version}\033[0m, channel: \033[92m{operator.channel}")
        
        click.secho("---------------------------------------------------------------------------------------------------------------------------", fg="green")

    

class SubscriptionHandler:
    def __init__(self, operators, catalog_source_ns, target_ns):
        self._operators = operators
        self._file_list = []
        self._download_folder = ".ibm-pak"
        self._catalog_sources_prefix = "catalog-sources"
        self._catalog_source_ns = catalog_source_ns
        self._target_ns = target_ns

    def download_and_prepare(self):
        Utils.sanity_check()
        click.secho("Downloading CASES...", fg="green")
        try:
            click.secho("Removing .ibm-pak folder...", fg="green")
            shutil.rmtree(self._download_folder)
        except:
            pass
        for name, operator in self._operators.items():
            click.secho(f"\nDownloading {operator.name}...", fg="green")
            proc = subprocess.run(operator.command, shell=True)

        # Locate all file_name(s) under the specified directory
        self._file_list = [os.path.join(root, file) for root, dirs, files in os.walk(
            self._download_folder) for file in files if file.startswith(self._catalog_sources_prefix)]
            
        catalog_sources = []
        click.secho("\nStripping namespace from all catalog-sources yaml files:", fg="green")
        for file_path in self._file_list:
            with fileinput.FileInput(file_path, inplace=True) as file:
                # Iterate through each line in the file
                for line in file:
                    # Remove lines starting with "namespace:" (ignoring leading spaces)
                    if not line.lstrip().startswith("namespace:"):
                        print(line, end="")
                    if line.lstrip().startswith("name:"):
                        catalog_sources.append(line.split(":")[1].strip())
            click.secho(f"   {file_path}")
        
        for name in self._operators.keys():
            for catalog in catalog_sources:
                if name.split("-", 1)[1] in catalog:
                    self._operators.get(name).catalog_source_name = catalog

        
    def handle_namespaces(self):
        namespaces = [ self._catalog_source_ns, self._target_ns ]
        namespaces = list(set(namespaces)) #remove duplicates
        namespace_to_create = []
        for ns in namespaces:
            if subprocess.run(["oc", "get", "ns", ns], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
                namespace_to_create.append(f"oc new-project {ns}")
        if namespace_to_create: Utils.run_commands(namespace_to_create)

        if self._target_ns != "openshift-operators":
            click.secho(f"\nOperators will be installed in {self._target_ns} - OperatorGroup resource needed", fg="yellow")
            content = f"""apiVersion: operators.coreos.com/v1
kind: OperatorGroup
metadata:
  name: ibm-integration-operatorgroup
  namespace: {self._target_ns}
spec:
  targetNamespaces:
  - {self._target_ns}
"""
            filename = "operatorgroup-" + self._target_ns + ".yaml"
            click.secho(f"OperatorGroup for {self._target_ns} will be written to {filename}: ", fg="green")
            click.secho(f"\n{content}")
            with open(filename, 'w') as file:
                file.write(content)
            Utils.run_commands([f"oc apply -n {ns} -f {filename}"])

    def apply_catalog_sources(self):
        click.secho("\nApplying catalog sources...", fg="green")
        self.handle_namespaces()
        oc_commands = []
        for file in self._file_list:
            oc_commands.append(f"oc apply -n {self._catalog_source_ns} -f {file}")
        Utils.run_commands(oc_commands)

    def apply_subscriptions(self):
        click.secho("\nApplying subscriptions...", fg="green")
        oc_commands = []
        for name, operator in self._operators.items():
            sub = f"""apiVersion: operators.coreos.com/v1alpha1
kind: Subscription
metadata:
  name: {operator.name} 
spec:
  channel: {operator.channel}
  name: {operator.name} 
  source: {operator.catalog_source_name}
  sourceNamespace: {self._catalog_source_ns}
""" 
            filename = "subscription-" + operator.name + ".yaml"
            click.secho(
                f"\nSubscription for {operator.name} will be written to {filename}: ", fg="green")
            click.secho(f"{sub}")
            with open(filename, 'w') as file:
                file.write(sub)
                oc_commands.append(f"oc apply -n {self._target_ns} -f {filename}")
        Utils.run_commands(oc_commands)

class Operator:
    def __init__(self, friendly_name, export_command):
        self.command = export_command + " && export IBMPAK_HOME=. && oc ibm-pak get $CASE_NAME --version $CASE_VERSION && oc ibm-pak generate mirror-manifests $CASE_NAME icr.io --version $CASE_VERSION"
        self.friendly_name = friendly_name
        self.name = None
        self.channel = None
        self.version = None
        self.catalog_source_name = None

        pattern = r'export CASE_NAME=([^\s]+)'
        match = re.search(pattern, self.command)
        if match:
            self.name = match.group(1)
        pattern = r'export CASE_VERSION=([^\s]+)'
        match = re.search(pattern, self.command)
        if match:
            self.version = match.group(1)



    def print(self):
        print(f"""
                name: {self.name}
                friendly_name: {self.friendly_name}
                version: {self.version}
                channel: {self.channel}
                catalog_source_name: {self.catalog_source_name}
                command: {self.command}""")

    
class Utils:
    def sanity_check():
        click.secho("Checking oc is installed...", fg="green")
        subprocess.run(["oc"], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
  
        click.secho("Checking oc is logged-in...", fg="green")
        if subprocess.run(["oc", "cluster-info"], stdout=subprocess.DEVNULL).returncode != 0:
            raise Exception ("oc is not logged-in. Make sure you are loggged-in to the correct cluster")
        
        click.secho("Checking ibm-pak is installed...", fg="green")
        if subprocess.run(["oc", "ibm-pak"], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT).returncode != 0:
            raise Exception("ibm-pak oc module is not installed. Get ibm-pak from https://github.com/IBM/ibm-pak#readme")  
        
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
                proc = subprocess.run(cmd, shell=True,)
        print("")



@click.group()
def main():
    return True




@main.command('deploy-operators', short_help="Connects to CP4I IBM documentation, downloads CASE files, installs catalog sources and operators in the requested namespaces and applies the OperatorGroup resource")
@click.option('--version', help="The CP4I version, e.g. 2023.2", required=True)
@click.option('--list', is_flag=True, help="List all operators and versions")
@click.option('--catalog_source_ns', default="openshift-marketplace", help='(Experimental) The namespace to apply the catalogsources (default: openshift-marketplace)')
@click.option('--target_ns', default="openshift-operators", help='The namespace to deploy the operator subscriptons (default: openshift-operators, i.e. All Namespaces)')
@click.option('--operator', '-o', multiple=True, default=["all"], help='operator to apply (default: all)')
def operator_handler(version, catalog_source_ns, target_ns, operator, list):
    
    try:
        operator_handler = OperatorHandler(version)
        operator_handler.populate()
        operator_handler.print()
        
        if list is True: sys.exit(0)

        operator_handler.filter(operator)

        click.secho("Will deploy following operators: ")
        click.secho("   \n   ".join(operator_handler.get_operators().keys()), fg="green")
        click.secho(f"\nCatalog sources will be applied in: ", nl=False)
        click.secho(catalog_source_ns, fg="green")
        click.secho(f"Operators will be deployed in: ", nl=False)
        click.secho(target_ns + "\n", fg="green")
        click.secho(f"Cloud Pak for Integration: ", nl=False)
        click.secho(version + "\n", fg="green")

        input("ENTER to continue, Ctrl-C to abort ")
        click.secho()

        sub_handler = SubscriptionHandler(operator_handler.get_operators(), catalog_source_ns, target_ns)
        sub_handler.download_and_prepare()
        sub_handler.apply_catalog_sources()
        sub_handler.apply_subscriptions()

       

    except Exception as e:
        click.secho(f"\nError: {e}\n", fg="red")
        traceback.print_exc()
    sys.exit(1)

    if not Utils.download_cases(Operators):
        return
    print("")
    
    if target_ns != "openshift-operators":
        click.secho(f"\nOperators will be installed in {target_ns} - OperatorGroup resource needed", fg="yellow")
        Utils.oc_apply_operator_group(target_ns)
    oc_apply_subscriptions(version, Operators, catalog_source_ns, target_ns)







if __name__ == '__main__':
    main()
