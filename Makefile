.PHONY: build clean install test help linux-amd64 linux-arm64 darwin-amd64 darwin-arm64 windows-amd64 windows-arm64 all-platforms

# Binary name
BINARY_NAME=cp4i-operators-installer
VERSION?=1.0.0

# Build the binary for the current platform
build:
	@echo "Building $(BINARY_NAME)..."
	go build -ldflags="-s -w" -o $(BINARY_NAME) main.go
	@echo "Build complete: $(BINARY_NAME)"

# Build for Linux AMD64
linux-amd64:
	@echo "Building for Linux AMD64..."
	GOOS=linux GOARCH=amd64 go build -ldflags="-s -w" -o $(BINARY_NAME)-linux-amd64 main.go
	@echo "Build complete: $(BINARY_NAME)-linux-amd64"

# Build for Linux ARM64
linux-arm64:
	@echo "Building for Linux ARM64..."
	GOOS=linux GOARCH=arm64 go build -ldflags="-s -w" -o $(BINARY_NAME)-linux-arm64 main.go
	@echo "Build complete: $(BINARY_NAME)-linux-arm64"

# Build for macOS AMD64
darwin-amd64:
	@echo "Building for macOS AMD64..."
	GOOS=darwin GOARCH=amd64 go build -ldflags="-s -w" -o $(BINARY_NAME)-darwin-amd64 main.go
	@echo "Build complete: $(BINARY_NAME)-darwin-amd64"

# Build for macOS ARM64 (Apple Silicon)
darwin-arm64:
	@echo "Building for macOS ARM64..."
	GOOS=darwin GOARCH=arm64 go build -ldflags="-s -w" -o $(BINARY_NAME)-darwin-arm64 main.go
	@echo "Build complete: $(BINARY_NAME)-darwin-arm64"

# Build for Windows AMD64
windows-amd64:
	@echo "Building for Windows AMD64..."
	GOOS=windows GOARCH=amd64 go build -ldflags="-s -w" -o $(BINARY_NAME)-windows-amd64.exe main.go
	@echo "Build complete: $(BINARY_NAME)-windows-amd64.exe"

# Build for Windows ARM64
windows-arm64:
	@echo "Building for Windows ARM64..."
	GOOS=windows GOARCH=arm64 go build -ldflags="-s -w" -o $(BINARY_NAME)-windows-arm64.exe main.go
	@echo "Build complete: $(BINARY_NAME)-windows-arm64.exe"

# Build for all platforms
all-platforms: linux-amd64 linux-arm64 darwin-amd64 darwin-arm64 windows-amd64 windows-arm64
	@echo "All platform builds complete!"

# Clean build artifacts
clean:
	@echo "Cleaning build artifacts..."
	rm -f $(BINARY_NAME)
	rm -f $(BINARY_NAME)-*
	rm -f *.yaml
	rm -rf .ibm-pak
	rm -f oc-ibm_pak*
	@echo "Clean complete"

# Install the binary to /usr/local/bin (requires sudo)
install: build
	@echo "Installing $(BINARY_NAME) to /usr/local/bin..."
	sudo cp $(BINARY_NAME) /usr/local/bin/
	@echo "Installation complete"

# Run tests
test:
	@echo "Running tests..."
	go test -v ./...

# Download dependencies
deps:
	@echo "Downloading dependencies..."
	go mod download
	go mod tidy
	@echo "Dependencies updated"

# Format code
fmt:
	@echo "Formatting code..."
	go fmt ./...
	@echo "Format complete"

# Run linter
lint:
	@echo "Running linter..."
	golangci-lint run
	@echo "Lint complete"

# Display help
help:
	@echo "CP4I Operators Installer - Makefile Commands"
	@echo ""
	@echo "Usage: make [target]"
	@echo ""
	@echo "Targets:"
	@echo "  build          Build binary for current platform"
	@echo "  linux-amd64    Build for Linux AMD64"
	@echo "  linux-arm64    Build for Linux ARM64"
	@echo "  darwin-amd64   Build for macOS AMD64"
	@echo "  darwin-arm64   Build for macOS ARM64 (Apple Silicon)"
	@echo "  windows-amd64  Build for Windows AMD64"
	@echo "  windows-arm64  Build for Windows ARM64"
	@echo "  all-platforms  Build for all supported platforms"
	@echo "  clean          Remove build artifacts"
	@echo "  install        Install binary to /usr/local/bin"
	@echo "  test           Run tests"
	@echo "  deps           Download and tidy dependencies"
	@echo "  fmt            Format Go code"
	@echo "  lint           Run linter (requires golangci-lint)"
	@echo "  help           Display this help message"