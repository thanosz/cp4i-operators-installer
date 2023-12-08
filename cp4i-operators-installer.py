#!/usr/bin/env python3.11

import pandas as panda
import click
import subprocess
import re
import shutil
import os
import fileinput
import sys
import traceback 
import time


class Operator:
    def __init__(self, friendly_name, export_command):
        self.command = export_command + ' && export IBMPAK_HOME=. && oc ibm-pak get $CASE_NAME --version $CASE_VERSION && oc ibm-pak generate mirror-manifests $CASE_NAME icr.io --version $CASE_VERSION'
        self.friendly_name = friendly_name
        self.name = None
        self.channel = None
        self.case_version = None
        self.catsrc_name = None

        pattern = r'export CASE_NAME=([^\s]+)'
        match = re.search(pattern, self.command)
        if match:
            self.name = match.group(1)
        pattern = r'export CASE_VERSION=([^\s]+)'
        match = re.search(pattern, self.command)
        if match:
            self.case_version = match.group(1)

    def print(self):
        print(f'''
                name: {self.name}
                friendly_name: {self.friendly_name}
                version: {self.case_version}
                channel: {self.channel}
                catalog_source_name: {self.catsrc_name}
                command: {self.command}''')

class Operators: # singleton holding a map of operators
    operators_map = {}
    def __new__(cls):
        if not hasattr(cls, '_instance'):
            cls._instance = super(Operators, cls).__new__(cls)
        return cls._instance


class OperatorHandler:
    def __init__(self, version):
        self.version = version
    
    def populate(self):
        command_url = f'https://www.ibm.com/docs/en/cloud-paks/cp-integration/{self.version}?topic=images-adding-catalog-sources-cluster'
        channel_url = f'https://www.ibm.com/docs/en/cloud-paks/cp-integration/{self.version}?topic=reference-operator-channel-versions-this-release'

        tmp_operators = {}
        try:
            click.secho(f'\nConnecting to {command_url}', fg='green')
            # get operator details from the doc page
            commands_table = panda.read_html(command_url, match='Export commands')[0]
            for i in range(0, len(commands_table.index)):
                friendly_name = commands_table.iloc[i, 0]
                export_command = commands_table.iloc[i, 1]
                operator = Operator(friendly_name, export_command)
                tmp_operators[friendly_name] = operator

            click.secho(f'Connecting to {channel_url}', fg='green')
            # get operator channels from the doc page
            channels_table = panda.read_html(channel_url, match='Capability name')[0]
            for i in range(0, len(channels_table.index)):
                friendly_name = channels_table.iloc[i, 1]
                if type(friendly_name) is not str:
                    continue
                channel = str(channels_table.iloc[i, 2]).split(',').pop()
                operator = tmp_operators.get(friendly_name)
                if operator is not None:
                    operator.channel = channel

            # change the map key from operator.friendly_name to operator.name
            for friendly_name, operator in tmp_operators.items():
                Operators().operators_map[operator.name] = operator
        except Exception as e:
            raise Exception(f'Is version {self.version} valid?, {e}')

    def filter(self, selection):
        if 'all' in selection: 
            click.secho('\nHINT: You can install individual operators by using -c flag mutliple times') 
        else: 
            filtered_operators = {}
            for name in selection:
                operator = Operators().operators_map.get(name)
                if operator is None: raise Exception(f"Operator '{name}' is not a valid operator name")
                filtered_operators[name] = operator
            Operators.operators_map = filtered_operators
        
        # datapower comes with ibm-apiconnect and if both specified, datapower fails
        if Operators().operators_map.get("ibm-apiconnect") is not None: 
            Operators().operators_map.pop("ibm-datapower-operator")
        # remove common-services as it comes with CP4I
        Operators().operators_map.pop("ibm-cp-common-services", None)
    
    def print(self):
        click.secho(f'\nOperators for CP4I version {self.version}', fg='green')
        click.secho('---------------------------------------------------------------------------------------------------------------------------', fg='green')    
        for name, operator in Operators().operators_map.items():
            click.secho(f'\033[92m{operator.name} \033[0m({operator.friendly_name}): CASE version: \033[92m{operator.case_version}\033[0m, channel: \033[92m{operator.channel}')
        click.secho('---------------------------------------------------------------------------------------------------------------------------', fg='green')

class SubscriptionHandler:
    def __init__(self, catsrc_ns, target_ns):
        self._file_list = []
        self._download_folder = '.ibm-pak'
        self._catsrc_file_prefix = 'catalog-sources'
        self._catsrc_ns = catsrc_ns
        self._target_ns = target_ns

    def download_and_prepare(self):
        click.secho('Downloading CASES...', fg='green')
        try:
            click.secho('Removing .ibm-pak folder...', fg='green')
            shutil.rmtree(self._download_folder)
        except:
            pass
        for name, operator in Operators().operators_map.items():
            click.secho(f'\nDownloading {operator.name}...', fg='green')
            proc = subprocess.run(operator.command, shell=True)

        # Locate all downloaded file_name(s) under the specified directory
        self._file_list = [os.path.join(root, file) for root, dirs, files in os.walk(
            self._download_folder) for file in files if file.startswith(self._catsrc_file_prefix)]
            
        catalog_sources = []
        click.secho('\nStripping namespace from all catalog-sources yaml files:', fg='green')
        for file_path in self._file_list:
            with fileinput.FileInput(file_path, inplace=True) as file:
                # Iterate through each line in the file
                for line in file:
                    # Remove lines starting with 'namespace:' (ignoring leading spaces)
                    if not line.lstrip().startswith('namespace:'):
                        print(line, end='')
                    # Get the catalog source name to be later used to bind the subscription
                    if line.lstrip().startswith('name:'):
                        catalog_sources.append(line.split(':')[1].strip())
            click.secho(f'   {file_path}')
        
        for name in Operators().operators_map.keys():
            for catalog in catalog_sources:
                if name.split('-')[1] in catalog:
                    Operators().operators_map.get(name).catsrc_name = catalog
    
    def apply_catalog_sources(self):
        click.secho('\nApplying catalog sources...', fg='green')
        self.handle_namespaces()
        oc_commands = []
        for file in self._file_list:
            oc_commands.append(f'oc apply -n {self._catsrc_ns} -f {file}')
        Utils.run_commands(oc_commands)

    def apply_subscriptions(self):
        click.secho('\nApplying subscriptions...', fg='green')
        oc_commands = []
        for name, operator in Operators().operators_map.items():
            sub = f'''apiVersion: operators.coreos.com/v1alpha1
kind: Subscription
metadata:
  name: {operator.name} 
spec:
  channel: {operator.channel}
  name: {operator.name} 
  source: {operator.catsrc_name}
  sourceNamespace: {self._catsrc_ns}
''' 
            filename = 'subscription-' + operator.name + '.yaml'
            click.secho(
                f'\nSubscription for {operator.name} will be written to {filename}: ', fg='green')
            click.secho(f'{sub}')
            with open(filename, 'w') as file:
                file.write(sub)
                oc_commands.append(f'oc apply -n {self._target_ns} -f {filename}')

        # Workarround to put at the end failing subscriptions that cause other subscriptions to fail as well
        # TODO investigate the failures.
        for s in 'ibm-integration-asset-repository', 'ibm-aspera-hsts-operator':
            for i, v in enumerate(oc_commands):
                if s in v: oc_commands.append(oc_commands.pop(i))
        Utils.run_commands(oc_commands, delay=60, extra_message='for the subscription to settle')


    def handle_namespaces(self):
        namespaces = [ self._catsrc_ns, self._target_ns ]
        namespaces = list(set(namespaces)) # remove duplicates
        namespace_to_create = []
        for ns in namespaces:
            if subprocess.run(['oc', 'get', 'ns', ns], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
                namespace_to_create.append(f'oc new-project {ns}')
        if namespace_to_create: Utils.run_commands(namespace_to_create)

        if self._target_ns != 'openshift-operators':
            click.secho(f'\nOperators will be installed in {self._target_ns} - OperatorGroup resource needed', fg='yellow')
            content = f'''apiVersion: operators.coreos.com/v1
kind: OperatorGroup
metadata:
  name: ibm-integration-operatorgroup
  namespace: {self._target_ns}
spec:
  targetNamespaces:
  - {self._target_ns}
'''
            filename = 'operatorgroup-' + self._target_ns + '.yaml'
            click.secho(f'OperatorGroup for {self._target_ns} will be written to {filename}: ', fg='green')
            click.secho(f'\n{content}')
            with open(filename, 'w') as file:
                file.write(content)
            Utils.run_commands([f'oc apply -n {self._target_ns} -f {filename}'])

    
class Utils:
    non_interactive = False
    def sanity_check():
        click.secho('Sanity check...', fg='green')

        click.secho('   Checking oc is installed...', fg='green')
        subprocess.run(['oc'], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
  
        click.secho('   Checking oc is logged-in...', fg='green')
        if subprocess.run(['oc', 'cluster-info'], stdout=subprocess.DEVNULL).returncode != 0:
            raise Exception ('oc is not logged-in. Make sure you are loggged-in to the correct cluster')
        
        click.secho('   Checking ibm-pak is installed...', fg='green')
        if subprocess.run(['oc', 'ibm-pak'], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT).returncode != 0:
            raise Exception('ibm-pak oc module is not installed. Get ibm-pak from https://github.com/IBM/ibm-pak#readme')  
        
        click.echo()
        
    def run_commands(oc_commands, delay=0, extra_message=''):
        click.secho('\nThe following will now run...', fg='green')
        for cmd in oc_commands:
            click.secho(f'   {cmd}', fg='yellow')

        click.secho('')
        if Utils.non_interactive is False:
            while True:
                answer = input(
                    "Press 'a' to apply the change, 'c' to continue without applying, Ctrl-C to abort: ")
                if answer == 'c':
                    return
                if answer == 'a':
                    break

        for cmd in oc_commands:
                proc = subprocess.run(cmd, shell=True)
                if delay > 0: 
                    click.secho(f'Sleeping {delay} seconds {extra_message}...', fg='green')
                    time.sleep(delay)
        print('')

@click.group()
def main():
    return True

@main.command('deploy-operators', short_help='Connects to CP4I IBM documentation, downloads CASE files, installs catalog sources and operators in the requested namespaces and applies the OperatorGroup resource')
@click.option('--version', help='The CP4I version, e.g. 2023.2', required=True)
@click.option('--list', is_flag=True, help='List all operators and versions')
@click.option('--namespaced', is_flag=True, default=False, help='(Experimental) If set the catalogsources will be applied to target_ns')
@click.option('--target_ns', default='openshift-operators', help='The namespace to deploy the operator subscriptons (default: openshift-operators, i.e. All Namespaces)')
@click.option('--operator', '-o', multiple=True, default=['all'], help='Operator to apply (default: all)')
@click.option('--noninteractive', is_flag=True, default=False, help='Do not ask for user confirmation and apply the changes')

def deploy_operators(version, namespaced, target_ns, operator, list, noninteractive):
    
    try:
        Utils.non_interactive = noninteractive
        operator_handler = OperatorHandler(version)
        operator_handler.populate()
        operator_handler.print()
        
        if list is True: sys.exit(0)

        operator_handler.filter(operator)

        catsrc_ns = 'openshift-marketplace'
        if namespaced is True:
            catsrc_ns = target_ns
        if catsrc_ns == 'openshift-operators':
            click.secho('You specified --namespaced but, but you did not specify --target_ns. Refusing to continue\n', fg='red')
            sys.exit(2)
        
        click.secho('\nWill deploy following operators: ')
        click.secho('   ' + '\n   '.join(Operators.operators_map.keys()), fg='green')
        click.secho(f'\nCatalog sources will be applied in: ', nl=False)
        click.secho(catsrc_ns, fg='red' if catsrc_ns != 'openshift-marketplace' else 'green')
        click.secho(f'Operators will be deployed in: ', nl=False)
        click.secho(target_ns + '\n', fg='green')

        if Utils.non_interactive is False:
            click.secho('ENTER to continue, Ctrl-C to abort ')
            input()

        Utils.sanity_check()
        subs_handler = SubscriptionHandler(catsrc_ns, target_ns)
        subs_handler.download_and_prepare()
        subs_handler.apply_catalog_sources()
        subs_handler.apply_subscriptions()

    except Exception as e:
        click.secho(f'\nError: {e}\n', fg='red')
        traceback.print_exc()
        sys.exit(1)

    sys.exit(0)

if __name__ == '__main__':
    main()
