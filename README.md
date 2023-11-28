# Overview

cp4i-operators-installer is a helper python script to automate the installation of Cloud Pak for Integration operators in an Openshift cluster. Given a CP4I version, it will connect to the respective documentation page, extract the needed information, download the appropriate CASE files and generate/apply the catalog sources and subscription yaml files to track the specific CP4I version channel.

You also have the option to designate the namespaces to which the catalog-sources and subscriptions should be applied (by default, openshift-markerplace and openshift-operarors.

To have an idea of how it works, click on the video link below

You will need python 3.11 and the required modules (```pip3 install -r requirements.txt```)

[![Watch the video](https://img.youtube.com/vi/JDQ1kJDeUwk/hqdefault.jpg)](https://youtu.be/JDQ1kJDeUwk)

