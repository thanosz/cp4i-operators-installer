#!/usr/bin/env python3

import click
import subprocess
import re
import shutil
import os
import fileinput
import sys
import traceback 
import time
from bs4 import BeautifulSoup
import requests
import yaml
import tarfile


class Operator: 
    # Class to hold the Operator attributes.
    def __init__(self, friendly_name, literal_name):
        self.friendly_name = friendly_name
        self.literal_name = literal_name
        self.channel = None
        self.case_name = None
        self.case_version = None
        self.catsrc_name = None
        self.catsrc_files = []
        self.command = None

    def set_command(self, export_command):
        
        if 'oc apply' in export_command:
            # 16.1.0 documentation changed the commands and replaced the use of ibm-pak to directly applying the catsrc from a file in public github
            # Instead of changing the whole logic of the app, we generate an ibm-pak command as it was previously expected
            self.case_name = self.get_matched_pattern(r'/([a-zA-Z0-9\-\_]+)/(\d+\.\d+\.\d+)/', export_command)
            self.case_version = self.get_matched_pattern(r'/(\d+\.\d+\.\d+)/', export_command)
        else:
            self.case_name = self.get_matched_pattern(r'export .*_NAME=([^\s]+)', export_command)
            self.case_version = self.get_matched_pattern(r'export .*_VERSION=([^\s]+)', export_command)
           
        self.command = f'export IBMPAK_HOME=. && ./oc-ibm_pak get {self.case_name} --version {self.case_version} && ./oc-ibm_pak generate mirror-manifests {self.case_name} icr.io --version {self.case_version}'

    def get_matched_pattern(self, pattern, input_str):
         match = re.search(pattern, input_str)
         if not match:
             raise(Exception(f'Could not match "{pattern}" for input string "{input_str}"'))
         return match.group(1)
             
    def print(self):
        print(f'''
        
        literal_name: {self.literal_name}
       friendly_name: {self.friendly_name}
           case_name: {self.case_name}
        case_version: {self.case_version}
             channel: {self.channel}
         catsrc_name: {self.catsrc_name}
        catsrc_files: {self.catsrc_files}
             command: {self.command}''')

class Operators: 
    # Singleton holding a map of operators
    _map = {}

    def map(self):
        return self._map
    
    def set(self, map):
        self._map = map
    
    def __new__(cls):
        if not hasattr(cls, '_instance'):
            cls._instance = super(Operators, cls).__new__(cls)
        return cls._instance


class OperatorHandler:
    # Connects to online documentation pages and discover the operator name (friendly - long name), the
    #   literal name (the actual operator name) and the CASE operator name (might differ from the literal name) and the CASE Versions.
    # Instantiate operator objects and add them to the Operators Map.
    def __init__(self, version):
        self.version = version
    
    def populate(self):
        curl_header = {'User-Agent': 'curl/8.6.0'} # IBM Seems to block evertyhing else 
        if self.version.startswith('202'): # for 2023.4, etc
            raise Exception ('Versions 202x are not supported. Use 202x branch instead')
       
        case_commands_url = f'https://www.ibm.com/docs/en/cloud-paks/cp-integration/{self.version}?topic=images-adding-catalog-sources-openshift-cluster'
        literal_operator_name_url = f'https://www.ibm.com/docs/en/cloud-paks/cp-integration/{self.version}?topic=operators-installing-by-using-cli'
        
        tmp_operators = {}
        try:

            click.echo()
            click.secho(f'Connecting to {literal_operator_name_url}', fg='green')
            response = requests.get(literal_operator_name_url, headers=curl_header)
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Find all list items (bullets)
            bullets = soup.find_all('li')
            for bullet in bullets:
                # Extract bullet text
                friendly_name = bullet.get_text(strip=True).split('-',1)[0]

                # Find the next code block after the bullet
                code_block = bullet.find_next('pre')
                if code_block:
                    code_text = code_block.get_text() 
                    # Attempt to parse YAML and extract metadata.name
                    try:
                        # Clean up the code text to ensure it's valid YAML
                        yaml_content = yaml.safe_load(code_text)
                        if isinstance(yaml_content, dict):
                            metadata = yaml_content.get('metadata', {})
                            literal_name = metadata.get('name')
                            spec = yaml_content.get('spec', {})
                            channel = spec.get('channel')
                            cat_src = spec.get('source')
                        
                            operator = Operator(friendly_name, literal_name)

                            operator.channel = channel
                            operator.catsrc_name = cat_src
                            tmp_operators[friendly_name] = operator

                    except yaml.YAMLError as e:
                        print(f"YAML parsing error: {e}")

            click.secho(f'Connecting to {case_commands_url}', fg='green')
            
            response = requests.get(case_commands_url, headers=curl_header)
            soup = BeautifulSoup(response.content, 'html.parser')
            header = soup.find('h2', string='Catalog sources for operators')
            if header:
                ul = header.parent.find('ul')
                if ul:
                    for li in ul.find_all('li'):
                        result = li.text.split('\n')
                        friendly_name = result[0]
                        export_command = result[1]
                        operator = tmp_operators.get(friendly_name)
                        if operator:
                            operator.set_command(export_command)

           
            # change the map key from operator.friendly_name to operator.literal_name
            for friendly_name, operator in tmp_operators.items():
                if operator.case_version is not None:
                    Operators().map()[operator.literal_name] = operator
                #operator.print()
        except Exception as e:
            raise Exception(f'Is version {self.version} valid?, {e}')

    def filter(self, selection):
        if 'all' in selection: 
            click.secho('\nHINT: You can install individual operators by using -o flag mutliple times') 
        else: 
            filtered_operators = {}
            for name in selection:
                operator = Operators().map().get(name)
                if operator is None: raise Exception(f"Operator '{name}' is not a valid operator name")
                filtered_operators[name] = operator
            Operators().set(filtered_operators)
        
        # datapower comes with ibm-apiconnect and if both specified, datapower fails
        if Operators().map().get("ibm-apiconnect") is not None: 
            Operators().map().pop("datapower-operator", None)
        if Operators().map().get("ibm-eventstreams") is not None: 
            Operators().map().pop("ibm-eem-operator", None)
    
    def print(self):
        click.secho(f'\nOperators for CP4I version {self.version}', fg='green')
        click.secho('-------------------------------------------------------------------------------------------------------------------', fg='green')    
        for name, operator in Operators().map().items():
            click.secho(f'\033[92m{operator.literal_name} \033[0m({operator.friendly_name}): CASE version: \033[92m{operator.case_version}\033[0m, channel: \033[92m{operator.channel}')
            #operator.print()
        click.secho('------------------------------------------------------------------------------------------------------------------', fg='green')

class SubscriptionHandler:
    # SubscriptionHandler runs the commands according to the documentation for downloading the catalog sources for each operator
    # Strip the namespace from the cataog sources and record the catalog source name in the respective operator object
    # Creates and applies the OperatorGroup resource if the user requested the installation of operators in a specific namespace
    # Generates and applies the operator subscriptions files
    def __init__(self, catsrc_ns, target_ns):
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
        for operator in Operators().map().values():
            click.secho(f'\nDownloading {operator.literal_name}...', fg='green')
            proc = subprocess.run(operator.command, shell=True)
            operator.catsrc_files = [ os.path.join(root,file) 
                                     for root, dirs, files in os.walk(os.path.join(self._download_folder, "data", "mirror", operator.case_name)) 
                                      for file in files if file.startswith(self._catsrc_file_prefix) 
                                  ]
            
            catalog_sources = []
            for file_path in operator.catsrc_files:
                with fileinput.FileInput(file_path, inplace=True) as file:
                    # Iterate through each line in the file
                    for line in file:
                        # Remove lines starting with 'namespace:' (ignoring leading spaces)
                        if not line.lstrip().startswith('namespace:'):
                            print(line, end='')
                        # Get the catalog source name to be later used to bind the subscription
                        if line.lstrip().startswith('name:'):
                            catalog_sources.append(line.split(':')[1].strip())
                click.secho(f'Downloaded and stripped namespace from catalog-sources yaml file {file_path}', fg='green')
    
    def apply_catalog_sources(self):
        click.secho('\nApplying catalog sources...', fg='green')
        self.handle_namespaces()
        oc_commands = []
        for operator in Operators().map().values():
            for file in operator.catsrc_files:
                oc_commands.append(f'oc apply -n {self._catsrc_ns} -f {file}')
        Utils.run_commands(oc_commands)

    def apply_subscriptions(self):
        click.secho('\nApplying subscriptions...', fg='green')
        oc_commands = []
        for operator in Operators().map().values():
            sub = f'''apiVersion: operators.coreos.com/v1alpha1
kind: Subscription
metadata:
  name: {operator.literal_name} 
spec:
  channel: {operator.channel}
  name: {operator.literal_name} 
  source: {operator.catsrc_name}
  sourceNamespace: {self._catsrc_ns}
''' 
            filename = 'subscription-' + operator.literal_name + '.yaml'
            click.secho(
                f'\nSubscription for {operator.literal_name} will be written to {filename}: ', fg='green')
            click.secho(f'{sub}')
            with open(filename, 'w') as file:
                file.write(sub)
                oc_commands.append(f'oc apply -n {self._target_ns} -f {filename}')

        Utils.run_commands(oc_commands, delay=30, extra_message='for the subscription to settle')

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
            raise Exception ('oc is not logged-in. Make sure you are logged-in to the correct cluster')
        
        click.secho('   Checking for ibm-pak...', fg='green')
        if not os.path.exists('./oc-ibm_pak'):
            output = subprocess.check_output(['uname', '-s', '-m'], text=True).strip()
            os_name, arch = output.lower().split()
            if arch == "x86_64": arch = "amd64"
            url = f'https://github.com/IBM/ibm-pak/releases/download/v1.18.1/oc-ibm_pak-{os_name}-{arch}.tar.gz'
            click.secho(f'     Downloading ibm-pak... ({url})', fg='green')
            filename = f'oc-ibm_pak.tar.gz'
        
            with requests.get(url, stream=True) as r:
                r.raise_for_status()
                with open(filename, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
            with tarfile.open(filename, 'r:gz') as tar:
                tar.extractall(filter='data')  # You can set a custom extraction directory
                tar.close()        
            os.rename(f'oc-ibm_pak-{os_name}-{arch}', 'oc-ibm_pak')
        
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

@main.command('deploy', short_help='Connects to CP4I IBM documentation, downloads CASE files, installs catalog sources and operators in the requested namespaces and applies the OperatorGroup resource')
@click.option('--version', '-v', help='The CP4I version, e.g. 2023.2', required=True)
@click.option('--list', is_flag=True, help='List all operators and versions')
@click.option('--namespaced', is_flag=True, default=False, help='(Experimental) If set the catalogsources will be applied to target_ns (for testing only)')
@click.option('--target_ns', default='openshift-operators', help='The namespace to deploy the operator subscriptons (default: openshift-operators, i.e. All Namespaces)')
@click.option('--operator', '-o', multiple=True, default=['all'], help='Operator(s) to apply (default: all)')
@click.option('--noninteractive', is_flag=True, default=False, help='Do not ask for user confirmation and apply the changes')

def deploy(version, namespaced, target_ns, operator, list, noninteractive):
    
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
        click.secho('   ' + '\n   '.join(Operators().map().keys()), fg='green')
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
