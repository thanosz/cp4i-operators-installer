# CP4I Operators Installer

A command-line tool to automate the installation of Cloud Pak for Integration operators in an OpenShift cluster. Given a CP4I version, it connects to the respective documentation page, extracts the needed information, downloads the appropriate CASE files and generates/applies the catalog sources and subscription yaml files to track the specific CP4I version channel.

You also have the option to designate the namespaces to which the catalog-sources and subscriptions should be applied (by default, openshift-marketplace and openshift-operators).

## Demo

To have an idea of how it works, click on the video link below:

[![Watch the video](https://img.youtube.com/vi/JDQ1kJDeUwk/hqdefault.jpg)](https://youtu.be/JDQ1kJDeUwk)

## Installation & Usage

### Go Version (Recommended - Single Binary)

**Prerequisites:**
- OpenShift CLI (`oc`) installed and logged into your cluster
- Go 1.21+ (only for building from source)

**Build:**
```bash
# Build for current platform
make build

# Or manually
go build -o cp4i-operators-installer main.go

# Cross-compile for other platforms
make all-platforms
```

**Usage:**
```bash
# List all available operators
./cp4i-operators-installer deploy --version 16.1.0 --list

# Install all operators (interactive)
./cp4i-operators-installer deploy --version 16.1.0

# Install specific operators
./cp4i-operators-installer deploy --version 16.1.0 -o ibm-apiconnect -o ibm-mq

# Non-interactive mode
./cp4i-operators-installer deploy --version 16.1.0 --noninteractive

# Custom namespace
./cp4i-operators-installer deploy --version 16.1.0 --target_ns cp4i-operators
```

**Flags:**
- `--version, -v`: CP4I version (required, e.g., "16.1.0")
- `--list`: List all operators and versions
- `--operator, -o`: Operator(s) to install (default: "all", can be used multiple times)
- `--target_ns`: Target namespace for subscriptions (default: "openshift-operators")
- `--namespaced`: Apply catalog sources to target namespace (experimental)
- `--noninteractive`: Skip confirmation prompts
- `--notapply`: Dry-run mode (don't apply changes)

### Python Version (Legacy)

**Prerequisites:**
- Python >=3.11
- Required modules: `pip3 install -r requirements.txt`

**Usage:**
```bash
python3 cp4i-operators-installer.py deploy --version 16.1.0
```

## Features

- 🚀 **Go Version**: Single binary, no dependencies, faster execution
- 🔍 **Auto-Discovery**: Scrapes IBM documentation for operator metadata
- 📦 **CASE Management**: Downloads and prepares IBM CASE files
- 🎯 **Selective Installation**: Install all or specific operators
- 🔧 **Namespace Control**: Customize catalog source and operator namespaces
- 🎨 **User-Friendly**: Colored output and interactive prompts

## Development

See `Makefile` for build targets:
```bash
make help          # Show all available commands
make build         # Build for current platform
make all-platforms # Build for Linux/macOS (AMD64/ARM64)
make clean         # Remove build artifacts
```

