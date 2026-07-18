package main

import (
	"archive/tar"
	"bufio"
	"compress/gzip"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"sync"
	"time"

	"github.com/PuerkitoBio/goquery"
	"github.com/fatih/color"
	"github.com/spf13/cobra"
	"gopkg.in/yaml.v3"
)

// Global flags
var (
	nonInteractive bool
	notApply       bool
)

// ============================================================================
// Data Structures
// ============================================================================

// Operator holds the operator attributes
type Operator struct {
	FriendlyName string
	LiteralName  string
	Channel      string
	CaseName     string
	CaseVersion  string
	CatsrcName   string
	CatsrcFiles  []string
	Command      string
}

// SetCommand parses and sets the CASE command for the operator
func (o *Operator) SetCommand(exportCommand string) error {
	var caseName, caseVersion string

	if strings.Contains(exportCommand, "oc apply") {
		// 16.1.0 documentation changed to direct oc apply
		// Generate ibm-pak command as expected
		caseName = o.GetMatchedPattern(`/case/([^/]+)/`, exportCommand)
		caseVersion = o.GetMatchedPattern(`/case/[^/]+/([^/]+)/`, exportCommand)
	} else {
		caseName = o.GetMatchedPattern(`export .*_NAME=([^\s]+)`, exportCommand)
		caseVersion = o.GetMatchedPattern(`export .*_VERSION=([^\s]+)`, exportCommand)
	}

	if caseName == "" || caseVersion == "" {
		return fmt.Errorf("could not extract CASE name or version from command")
	}

	o.CaseName = caseName
	o.CaseVersion = caseVersion
	o.Command = fmt.Sprintf("export IBMPAK_HOME=. && ./oc-ibm_pak get %s --version %s && ./oc-ibm_pak generate mirror-manifests %s icr.io --version %s",
		caseName, caseVersion, caseName, caseVersion)

	return nil
}

// GetMatchedPattern extracts a pattern from input string using regex
func (o *Operator) GetMatchedPattern(pattern, input string) string {
	re := regexp.MustCompile(pattern)
	matches := re.FindStringSubmatch(input)
	if len(matches) > 1 {
		return matches[1]
	}
	return ""
}

// Print displays operator details
func (o *Operator) Print() {
	fmt.Printf(`
        literal_name: %s
       friendly_name: %s
           case_name: %s
        case_version: %s
             channel: %s
         catsrc_name: %s
        catsrc_files: %v
             command: %s
`, o.LiteralName, o.FriendlyName, o.CaseName, o.CaseVersion, o.Channel, o.CatsrcName, o.CatsrcFiles, o.Command)
}

// Operators is a singleton holding a map of operators
type Operators struct {
	operatorMap map[string]*Operator
	mu          sync.RWMutex
}

var (
	operatorsInstance *Operators
	operatorsOnce     sync.Once
)

// GetOperators returns the singleton instance
func GetOperators() *Operators {
	operatorsOnce.Do(func() {
		operatorsInstance = &Operators{
			operatorMap: make(map[string]*Operator),
		}
	})
	return operatorsInstance
}

// GetMap returns the operator map (thread-safe read)
func (ops *Operators) GetMap() map[string]*Operator {
	ops.mu.RLock()
	defer ops.mu.RUnlock()
	return ops.operatorMap
}

// SetMap sets the operator map (thread-safe write)
func (ops *Operators) SetMap(m map[string]*Operator) {
	ops.mu.Lock()
	defer ops.mu.Unlock()
	ops.operatorMap = m
}

// ============================================================================
// OperatorHandler - Web Scraping and Population
// ============================================================================

// OperatorHandler connects to IBM documentation and discovers operators
type OperatorHandler struct {
	version string
}

// NewOperatorHandler creates a new OperatorHandler
func NewOperatorHandler(version string) *OperatorHandler {
	return &OperatorHandler{version: version}
}

// Populate scrapes IBM docs and populates operators
func (h *OperatorHandler) Populate() error {
	if strings.HasPrefix(h.version, "202") {
		return fmt.Errorf("versions 202x are not supported. Use 202x branch instead")
	}

	caseCommandsURL := fmt.Sprintf("https://www.ibm.com/docs/en/cloud-paks/cp-integration/%s?topic=images-adding-catalog-sources-openshift-cluster", h.version)
	literalOperatorNameURL := fmt.Sprintf("https://www.ibm.com/docs/en/cloud-paks/cp-integration/%s?topic=operators-installing-by-using-cli", h.version)

	tmpOperators := make(map[string]*Operator)

	// Scrape operator names and metadata
	color.Green("\nConnecting to %s", literalOperatorNameURL)
	
	client := &http.Client{Timeout: 30 * time.Second}
	req, err := http.NewRequest("GET", literalOperatorNameURL, nil)
	if err != nil {
		return fmt.Errorf("failed to create request: %w", err)
	}
	req.Header.Set("User-Agent", "curl/8.6.0") // IBM blocks other agents

	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("failed to fetch operator names: %w", err)
	}
	defer resp.Body.Close()

	doc, err := goquery.NewDocumentFromReader(resp.Body)
	if err != nil {
		return fmt.Errorf("failed to parse HTML: %w", err)
	}

	// Find all list items (bullets)
	doc.Find("li").Each(func(i int, s *goquery.Selection) {
		text := s.Text()
		parts := strings.SplitN(text, "-", 2)
		if len(parts) == 0 {
			return
		}
		friendlyName := strings.TrimSpace(parts[0])

		// Find the next code block after the bullet
		codeBlock := s.Find("pre").First()
		if codeBlock.Length() == 0 {
			codeBlock = s.Next().Find("pre").First()
		}

		if codeBlock.Length() > 0 {
			codeText := codeBlock.Text()
			
			// Parse YAML
			var yamlContent map[string]interface{}
			if err := yaml.Unmarshal([]byte(codeText), &yamlContent); err == nil {
				if metadata, ok := yamlContent["metadata"].(map[string]interface{}); ok {
					if literalName, ok := metadata["name"].(string); ok {
						if spec, ok := yamlContent["spec"].(map[string]interface{}); ok {
							channel, _ := spec["channel"].(string)
							catsrc, _ := spec["source"].(string)

							operator := &Operator{
								FriendlyName: friendlyName,
								LiteralName:  literalName,
								Channel:      channel,
								CatsrcName:   catsrc,
							}
							tmpOperators[friendlyName] = operator
						}
					}
				}
			}
		}
	})

	// Scrape CASE commands
	color.Green("Connecting to %s", caseCommandsURL)
	
	req, err = http.NewRequest("GET", caseCommandsURL, nil)
	if err != nil {
		return fmt.Errorf("failed to create request: %w", err)
	}
	req.Header.Set("User-Agent", "curl/8.6.0")

	resp, err = client.Do(req)
	if err != nil {
		return fmt.Errorf("failed to fetch CASE commands: %w", err)
	}
	defer resp.Body.Close()

	doc, err = goquery.NewDocumentFromReader(resp.Body)
	if err != nil {
		return fmt.Errorf("failed to parse HTML: %w", err)
	}

	// Find the "Catalog sources for operators" section
	doc.Find("h2").Each(func(i int, s *goquery.Selection) {
		if strings.Contains(s.Text(), "Catalog sources for operators") {
			ul := s.Parent().Find("ul").First()
			ul.Find("li").Each(func(j int, li *goquery.Selection) {
				text := li.Text()
				lines := strings.Split(text, "\n")
				if len(lines) >= 2 {
					friendlyName := strings.TrimSpace(lines[0])
					exportCommand := strings.TrimSpace(lines[1])
					
					if operator, ok := tmpOperators[friendlyName]; ok {
						if err := operator.SetCommand(exportCommand); err == nil {
							// Successfully set command
						}
					}
				}
			})
		}
	})

	// Transfer to main operators map (only those with case_version)
	ops := GetOperators()
	finalMap := make(map[string]*Operator)
	for _, operator := range tmpOperators {
		if operator.CaseVersion != "" {
			finalMap[operator.LiteralName] = operator
		}
	}
	ops.SetMap(finalMap)

	if len(finalMap) == 0 {
		return fmt.Errorf("no operators found - is version %s valid?", h.version)
	}

	return nil
}

// Filter filters operators based on selection
func (h *OperatorHandler) Filter(selection []string) error {
	ops := GetOperators()
	currentMap := ops.GetMap()

	if len(selection) == 1 && selection[0] == "all" {
		color.Yellow("\nHINT: You can install individual operators by using -o flag multiple times")
	} else {
		filteredOperators := make(map[string]*Operator)
		for _, name := range selection {
			operator, ok := currentMap[name]
			if !ok {
				return fmt.Errorf("operator '%s' is not a valid operator name", name)
			}
			filteredOperators[name] = operator
		}
		ops.SetMap(filteredOperators)
	}

	// Handle special cases
	currentMap = ops.GetMap()
	if _, hasAPIConnect := currentMap["ibm-apiconnect"]; hasAPIConnect {
		delete(currentMap, "datapower-operator")
	}
	if _, hasEventStreams := currentMap["ibm-eventstreams"]; hasEventStreams {
		delete(currentMap, "ibm-eem-operator")
	}

	return nil
}

// Print displays the operator list
func (h *OperatorHandler) Print() {
	ops := GetOperators()
	currentMap := ops.GetMap()

	color.Green("\nOperators for CP4I version %s", h.version)
	color.Green("-------------------------------------------------------------------------------------------------------------------")
	for _, operator := range currentMap {
		fmt.Printf("\033[92m%s\033[0m (%s): CASE version: \033[92m%s\033[0m, channel: \033[92m%s\033[0m\n",
			operator.LiteralName, operator.FriendlyName, operator.CaseVersion, operator.Channel)
	}
	color.Green("-------------------------------------------------------------------------------------------------------------------")
}

// ============================================================================
// SubscriptionHandler - CASE Management and K8s Resources
// ============================================================================

// SubscriptionHandler manages CASE downloads and K8s resource application
type SubscriptionHandler struct {
	downloadFolder   string
	catsrcFilePrefix string
	catsrcNs         string
	targetNs         string
}

// NewSubscriptionHandler creates a new SubscriptionHandler
func NewSubscriptionHandler(catsrcNs, targetNs string) *SubscriptionHandler {
	return &SubscriptionHandler{
		downloadFolder:   ".ibm-pak",
		catsrcFilePrefix: "catalog-sources",
		catsrcNs:         catsrcNs,
		targetNs:         targetNs,
	}
}

// DownloadAndPrepare downloads CASE files and prepares catalog sources
func (sh *SubscriptionHandler) DownloadAndPrepare() error {
	color.Green("Downloading CASES...")

	// Remove existing .ibm-pak folder
	color.Green("Removing .ibm-pak folder...")
	os.RemoveAll(sh.downloadFolder)

	ops := GetOperators()
	for _, operator := range ops.GetMap() {
		color.Green("\nDownloading %s...", operator.LiteralName)
		
		cmd := exec.Command("sh", "-c", operator.Command)
		cmd.Stdout = os.Stdout
		cmd.Stderr = os.Stderr
		if err := cmd.Run(); err != nil {
			return fmt.Errorf("failed to download %s: %w", operator.LiteralName, err)
		}

		// Find catalog source files
		casePath := filepath.Join(sh.downloadFolder, "data", "mirror", operator.CaseName)
		var catsrcFiles []string
		
		err := filepath.Walk(casePath, func(path string, info os.FileInfo, err error) error {
			if err != nil {
				return err
			}
			if !info.IsDir() && strings.HasPrefix(info.Name(), sh.catsrcFilePrefix) {
				catsrcFiles = append(catsrcFiles, path)
			}
			return nil
		})
		
		if err != nil {
			return fmt.Errorf("failed to find catalog source files for %s: %w", operator.LiteralName, err)
		}

		operator.CatsrcFiles = catsrcFiles

		// Strip namespace from catalog source files
		for _, filePath := range catsrcFiles {
			if err := sh.stripNamespaceFromFile(filePath); err != nil {
				return fmt.Errorf("failed to process %s: %w", filePath, err)
			}
			color.Green("Downloaded and stripped namespace from catalog-sources yaml file %s", filePath)
		}
	}

	return nil
}

// stripNamespaceFromFile removes namespace lines from YAML file
func (sh *SubscriptionHandler) stripNamespaceFromFile(filePath string) error {
	input, err := os.ReadFile(filePath)
	if err != nil {
		return err
	}

	lines := strings.Split(string(input), "\n")
	var output []string

	for _, line := range lines {
		trimmed := strings.TrimSpace(line)
		if !strings.HasPrefix(trimmed, "namespace:") {
			output = append(output, line)
		}
	}

	return os.WriteFile(filePath, []byte(strings.Join(output, "\n")), 0644)
}

// HandleNamespaces creates namespaces and OperatorGroup if needed
func (sh *SubscriptionHandler) HandleNamespaces() error {
	namespaces := []string{sh.catsrcNs, sh.targetNs}
	
	// Remove duplicates
	nsMap := make(map[string]bool)
	for _, ns := range namespaces {
		nsMap[ns] = true
	}

	var namespacesToCreate []string
	for ns := range nsMap {
		cmd := exec.Command("oc", "get", "ns", ns)
		cmd.Stdout = nil
		cmd.Stderr = nil
		if err := cmd.Run(); err != nil {
			namespacesToCreate = append(namespacesToCreate, fmt.Sprintf("oc new-project %s", ns))
		}
	}

	if len(namespacesToCreate) > 0 {
		if err := RunCommands(namespacesToCreate, 0, ""); err != nil {
			return err
		}
	}

	// Create OperatorGroup if needed
	if sh.targetNs != "openshift-operators" {
		color.Yellow("\nOperators will be installed in %s - OperatorGroup resource needed", sh.targetNs)
		
		content := fmt.Sprintf(`apiVersion: operators.coreos.com/v1
kind: OperatorGroup
metadata:
  name: ibm-integration-operatorgroup
  namespace: %s
spec:
  targetNamespaces:
  - %s
`, sh.targetNs, sh.targetNs)

		filename := fmt.Sprintf("operatorgroup-%s.yaml", sh.targetNs)
		color.Green("OperatorGroup for %s will be written to %s:", sh.targetNs, filename)
		fmt.Printf("\n%s\n", content)

		if err := os.WriteFile(filename, []byte(content), 0644); err != nil {
			return fmt.Errorf("failed to write OperatorGroup file: %w", err)
		}

		return RunCommands([]string{fmt.Sprintf("oc apply -n %s -f %s", sh.targetNs, filename)}, 0, "")
	}

	return nil
}

// ApplyCatalogSources applies catalog sources to the cluster
func (sh *SubscriptionHandler) ApplyCatalogSources() error {
	color.Green("\nApplying catalog sources...")
	
	if err := sh.HandleNamespaces(); err != nil {
		return err
	}

	var ocCommands []string
	ops := GetOperators()
	for _, operator := range ops.GetMap() {
		for _, file := range operator.CatsrcFiles {
			ocCommands = append(ocCommands, fmt.Sprintf("oc apply -n %s -f %s", sh.catsrcNs, file))
		}
	}

	return RunCommands(ocCommands, 0, "")
}

// ApplySubscriptions generates and applies operator subscriptions
func (sh *SubscriptionHandler) ApplySubscriptions() error {
	color.Green("\nApplying subscriptions...")

	var ocCommands []string
	ops := GetOperators()
	for _, operator := range ops.GetMap() {
		sub := fmt.Sprintf(`apiVersion: operators.coreos.com/v1alpha1
kind: Subscription
metadata:
  name: %s
spec:
  channel: %s
  name: %s
  source: %s
  sourceNamespace: %s
`, operator.LiteralName, operator.Channel, operator.LiteralName, operator.CatsrcName, sh.catsrcNs)

		filename := fmt.Sprintf("subscription-%s.yaml", operator.LiteralName)
		color.Green("\nSubscription for %s will be written to %s:", operator.LiteralName, filename)
		fmt.Println(sub)

		if err := os.WriteFile(filename, []byte(sub), 0644); err != nil {
			return fmt.Errorf("failed to write subscription file: %w", err)
		}

		ocCommands = append(ocCommands, fmt.Sprintf("oc apply -n %s -f %s", sh.targetNs, filename))
	}

	return RunCommands(ocCommands, 30, "for the subscription to settle")
}

// ============================================================================
// Utility Functions
// ============================================================================

// SanityCheck verifies prerequisites
func SanityCheck() error {
	color.Green("Sanity check...")

	// Check oc binary
	color.Green("   Checking oc is installed...")
	cmd := exec.Command("oc")
	cmd.Stdout = nil
	cmd.Stderr = nil
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("oc command not found - please install OpenShift CLI")
	}

	// Check oc is logged in
	color.Green("   Checking oc is logged-in...")
	cmd = exec.Command("oc", "cluster-info")
	cmd.Stdout = nil
	cmd.Stderr = nil
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("oc is not logged-in. Make sure you are logged-in to the correct cluster")
	}

	// Check for ibm-pak
	color.Green("   Checking for ibm-pak...")
	if _, err := os.Stat("./oc-ibm_pak"); os.IsNotExist(err) {
		if err := downloadIBMPak(); err != nil {
			return fmt.Errorf("failed to download ibm-pak: %w", err)
		}
	}

	fmt.Println()
	return nil
}

// downloadIBMPak downloads the ibm-pak binary
func downloadIBMPak() error {
	osName := strings.ToLower(runtime.GOOS)
	arch := runtime.GOARCH
	if arch == "amd64" {
		arch = "amd64"
	}

	url := fmt.Sprintf("https://github.com/IBM/ibm-pak/releases/download/v1.24.0/oc-ibm_pak-%s-%s.tar.gz", osName, arch)
	color.Green("     Downloading ibm-pak... (%s)", url)

	resp, err := http.Get(url)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	filename := "oc-ibm_pak.tar.gz"
	out, err := os.Create(filename)
	if err != nil {
		return err
	}
	defer out.Close()

	if _, err := io.Copy(out, resp.Body); err != nil {
		return err
	}
	out.Close()

	// Extract tar.gz
	if err := extractTarGz(filename); err != nil {
		return err
	}

	// Rename binary
	oldName := fmt.Sprintf("oc-ibm_pak-%s-%s", osName, arch)
	if err := os.Rename(oldName, "oc-ibm_pak"); err != nil {
		return err
	}

	// Make executable
	if err := os.Chmod("oc-ibm_pak", 0755); err != nil {
		return err
	}

	// Clean up
	os.Remove(filename)

	return nil
}

// extractTarGz extracts a tar.gz file
func extractTarGz(filename string) error {
	file, err := os.Open(filename)
	if err != nil {
		return err
	}
	defer file.Close()

	gzr, err := gzip.NewReader(file)
	if err != nil {
		return err
	}
	defer gzr.Close()

	tr := tar.NewReader(gzr)

	for {
		header, err := tr.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return err
		}

		target := header.Name

		switch header.Typeflag {
		case tar.TypeReg:
			outFile, err := os.Create(target)
			if err != nil {
				return err
			}
			if _, err := io.Copy(outFile, tr); err != nil {
				outFile.Close()
				return err
			}
			outFile.Close()
		}
	}

	return nil
}

// RunCommands displays and executes commands
func RunCommands(commands []string, delay int, extraMsg string) error {
	if notApply {
		color.Yellow("\nSkipped...")
		return nil
	}

	color.Green("\nThe following will now run...")
	for _, cmd := range commands {
		color.Yellow("   %s", cmd)
	}

	fmt.Println()
	if !nonInteractive {
		reader := bufio.NewReader(os.Stdin)
		for {
			fmt.Print("Press 'a' to apply the change, 'c' to continue without applying, Ctrl-C to abort: ")
			answer, _ := reader.ReadString('\n')
			answer = strings.TrimSpace(answer)
			
			if answer == "c" {
				return nil
			}
			if answer == "a" {
				break
			}
		}
	}

	for _, cmdStr := range commands {
		cmd := exec.Command("sh", "-c", cmdStr)
		cmd.Stdout = os.Stdout
		cmd.Stderr = os.Stderr
		if err := cmd.Run(); err != nil {
			return fmt.Errorf("command failed: %s: %w", cmdStr, err)
		}

		if delay > 0 {
			color.Green("Sleeping %d seconds %s...", delay, extraMsg)
			time.Sleep(time.Duration(delay) * time.Second)
		}
	}

	fmt.Println()
	return nil
}

// ============================================================================
// CLI Commands
// ============================================================================

var rootCmd = &cobra.Command{
	Use:   "cp4i-operators-installer",
	Short: "Automate CP4I operator installation on OpenShift",
	Long:  `CP4I Operators Installer connects to IBM documentation, downloads CASE files, and installs operators on OpenShift clusters.`,
}

var deployCmd = &cobra.Command{
	Use:   "deploy",
	Short: "Deploy CP4I operators",
	Long:  `Connects to CP4I IBM documentation, downloads CASE files, installs catalog sources and operators in the requested namespaces and applies the OperatorGroup resource`,
	RunE:  runDeploy,
}

var (
	flagVersion        string
	flagList           bool
	flagNamespaced     bool
	flagTargetNs       string
	flagOperators      []string
	flagNonInteractive bool
	flagNotApply       bool
)

func init() {
	deployCmd.Flags().StringVarP(&flagVersion, "version", "v", "", "The CP4I version, e.g. 16.1.0 (required)")
	deployCmd.MarkFlagRequired("version")
	
	deployCmd.Flags().BoolVar(&flagList, "list", false, "List all operators and versions")
	deployCmd.Flags().BoolVar(&flagNamespaced, "namespaced", false, "(Experimental) If set the catalogsources will be applied to target_ns (for testing only)")
	deployCmd.Flags().StringVar(&flagTargetNs, "target_ns", "openshift-operators", "The namespace to deploy the operator subscriptions (default: openshift-operators, i.e. All Namespaces)")
	deployCmd.Flags().StringSliceVarP(&flagOperators, "operator", "o", []string{"all"}, "Operator(s) to apply (default: all)")
	deployCmd.Flags().BoolVar(&flagNonInteractive, "noninteractive", false, "Do not ask for user confirmation and apply the changes")
	deployCmd.Flags().BoolVar(&flagNotApply, "notapply", false, "Do not apply the catalog sources and subscriptions")

	rootCmd.AddCommand(deployCmd)
}

func runDeploy(cmd *cobra.Command, args []string) error {
	// Set global flags
	nonInteractive = flagNonInteractive
	notApply = flagNotApply

	// Create operator handler and populate
	operatorHandler := NewOperatorHandler(flagVersion)
	if err := operatorHandler.Populate(); err != nil {
		return err
	}

	operatorHandler.Print()

	// If list flag is set, exit
	if flagList {
		return nil
	}

	// Filter operators
	if err := operatorHandler.Filter(flagOperators); err != nil {
		return err
	}

	// Determine catalog source namespace
	catsrcNs := "openshift-marketplace"
	if flagNamespaced {
		catsrcNs = flagTargetNs
		if catsrcNs == "openshift-operators" {
			color.Red("You specified --namespaced but, but you did not specify --target_ns. Refusing to continue\n")
			os.Exit(2)
		}
	}

	// Show deployment summary
	fmt.Println("\nWill deploy following operators:")
	ops := GetOperators()
	for name := range ops.GetMap() {
		color.Green("   %s", name)
	}

	fmt.Print("\nCatalog sources will be applied in: ")
	if catsrcNs != "openshift-marketplace" {
		color.Red(catsrcNs)
	} else {
		color.Green(catsrcNs)
	}

	fmt.Print("Operators will be deployed in: ")
	color.Green("%s\n", flagTargetNs)

	// Confirm before proceeding
	if !nonInteractive {
		fmt.Println("\nENTER to continue, Ctrl-C to abort")
		bufio.NewReader(os.Stdin).ReadString('\n')
	}

	// Run sanity check
	if err := SanityCheck(); err != nil {
		return err
	}

	// Create subscription handler
	subsHandler := NewSubscriptionHandler(catsrcNs, flagTargetNs)

	// Download and prepare
	if err := subsHandler.DownloadAndPrepare(); err != nil {
		return err
	}

	// Apply catalog sources
	if err := subsHandler.ApplyCatalogSources(); err != nil {
		return err
	}

	// Apply subscriptions
	if err := subsHandler.ApplySubscriptions(); err != nil {
		return err
	}

	return nil
}

func main() {
	if err := rootCmd.Execute(); err != nil {
		color.Red("\nError: %v\n", err)
		os.Exit(1)
	}
}

// Made with Bob
