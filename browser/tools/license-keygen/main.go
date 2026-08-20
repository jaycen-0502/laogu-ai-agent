package main

import (
	"bufio"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"ant-chrome/backend/licenseissuer"
)

type ledgerFile struct {
	Licenses map[string]ledgerRecord `json:"licenses"`
}

type ledgerRecord struct {
	LicenseID string `json:"licenseId"`
	Customer  string `json:"customer"`
	DeviceID  string `json:"deviceId"`
	IssuedAt  string `json:"issuedAt"`
	ExpiresAt string `json:"expiresAt"`
	UnboundAt string `json:"unboundAt,omitempty"`
}

func main() {
	ensureConsole()
	if len(os.Args) < 2 {
		interactive()
		return
	}
	switch strings.ToLower(strings.TrimSpace(os.Args[1])) {
	case "init":
		initCommand(os.Args[2:])
	case "issue":
		issueCommand(os.Args[2:])
	case "inspect":
		inspectCommand(os.Args[2:])
	case "unbind":
		unbindCommand(os.Args[2:])
	default:
		fatal("用法: Laogu-License-Keygen.exe [init|issue|inspect|unbind]")
	}
}

func ensureConsole() {
	if console, err := os.OpenFile("CONOUT$", os.O_WRONLY, 0); err == nil {
		os.Stdout = console
		os.Stderr = console
	}
	if console, err := os.OpenFile("CONIN$", os.O_RDONLY, 0); err == nil {
		os.Stdin = console
	}
}

func initCommand(args []string) {
	fs := flag.NewFlagSet("init", flag.ExitOnError)
	keyPath := fs.String("key", defaultKeyPath(), "私钥文件路径")
	_ = fs.Parse(args)
	publicKey, err := licenseissuer.GenerateIssuerKeyFile(*keyPath)
	if err != nil {
		fatal(err.Error())
	}
	fmt.Println("管理员私钥已生成：", *keyPath)
	fmt.Println("请备份该文件，丢失后无法为现有客户端续期。")
	fmt.Println("客户端公钥：", publicKey)
}

func issueCommand(args []string) {
	fs := flag.NewFlagSet("issue", flag.ExitOnError)
	keyPath := fs.String("key", defaultKeyPath(), "私钥文件路径")
	requestCode := fs.String("request", "", "客户请求码")
	days := fs.Int("days", 7, "授权天数")
	customer := fs.String("customer", "", "客户备注")
	licenseID := fs.String("license", "", "许可证编号，同一编号只允许绑定一台电脑")
	_ = fs.Parse(args)
	code, payload := issue(*keyPath, *requestCode, *days, *customer, *licenseID)
	printIssued(code, payload)
}

func inspectCommand(args []string) {
	fs := flag.NewFlagSet("inspect", flag.ExitOnError)
	requestCode := fs.String("request", "", "客户请求码")
	_ = fs.Parse(args)
	payload, err := licenseissuer.DecodeRequest(*requestCode)
	if err != nil {
		fatal(err.Error())
	}
	raw, _ := json.MarshalIndent(payload, "", "  ")
	fmt.Println(string(raw))
}

func unbindCommand(args []string) {
	fs := flag.NewFlagSet("unbind", flag.ExitOnError)
	licenseID := fs.String("license", "", "要解绑的许可证编号")
	_ = fs.Parse(args)
	id := strings.TrimSpace(*licenseID)
	if id == "" {
		fatal("许可证编号不能为空")
	}
	ledger, err := loadLedger(defaultLedgerPath())
	if err != nil {
		fatal(err.Error())
	}
	record, exists := ledger.Licenses[id]
	if !exists {
		fatal("未找到该许可证编号")
	}
	record.DeviceID = ""
	record.UnboundAt = time.Now().UTC().Format(time.RFC3339)
	ledger.Licenses[id] = record
	if err := saveLedger(defaultLedgerPath(), ledger); err != nil {
		fatal(err.Error())
	}
	fmt.Println("已解绑许可证：", id)
}

func interactive() {
	reader := bufio.NewReader(os.Stdin)
	fmt.Println("老谷浏览器离线授权工具")
	fmt.Println("私钥文件：", defaultKeyPath())
	if _, err := os.Stat(defaultKeyPath()); os.IsNotExist(err) {
		fmt.Println("未找到管理员私钥，请把配套的 Laogu-License-Issuer.key 放到本工具旁边。")
		pause(reader)
		return
	}

	fmt.Print("粘贴客户请求码：")
	requestCode, _ := reader.ReadString('\n')
	requestCode = strings.TrimSpace(requestCode)
	fmt.Print("授权天数 [7]：")
	daysText, _ := reader.ReadString('\n')
	days := 7
	if parsed, err := strconv.Atoi(strings.TrimSpace(daysText)); err == nil && parsed > 0 {
		days = parsed
	}
	fmt.Print("客户备注（可留空）：")
	customer, _ := reader.ReadString('\n')
	fmt.Print("许可证编号（必填，例如手机号或订单号）：")
	licenseID, _ := reader.ReadString('\n')
	code, payload := issue(defaultKeyPath(), requestCode, days, strings.TrimSpace(customer), strings.TrimSpace(licenseID))
	printIssued(code, payload)
	if err := clipboardSetText(code); err == nil {
		fmt.Println("激活码已自动复制到剪贴板。")
	}
	pause(reader)
}

func issue(keyPath, requestCode string, days int, customer, licenseID string) (string, licenseissuer.ActivationPayload) {
	licenseID = strings.TrimSpace(licenseID)
	if licenseID == "" {
		fatal("许可证编号不能为空；续期必须继续使用同一个编号")
	}
	request, err := licenseissuer.DecodeRequest(requestCode)
	if err != nil {
		fatal(err.Error())
	}
	ledger, err := loadLedger(defaultLedgerPath())
	if err != nil {
		fatal(err.Error())
	}
	if existing, ok := ledger.Licenses[licenseID]; ok && existing.DeviceID != "" && existing.DeviceID != request.DeviceID {
		fatal("该许可证已绑定其他电脑，请先执行 unbind 管理员解绑")
	}
	privateKey, err := licenseissuer.LoadIssuerPrivateKey(keyPath)
	if err != nil {
		fatal(err.Error())
	}
	code, payload, err := licenseissuer.IssueActivation(requestCode, privateKey, licenseissuer.IssueOptions{
		LicenseID: licenseID,
		Customer:  customer,
		Days:      days,
		Features:  []string{"browser", "playwright", "external_api"},
	}, time.Now())
	if err != nil {
		fatal(err.Error())
	}
	ledger.Licenses[licenseID] = ledgerRecord{
		LicenseID: licenseID,
		Customer:  customer,
		DeviceID:  request.DeviceID,
		IssuedAt:  payload.IssuedAt,
		ExpiresAt: payload.ExpiresAt,
	}
	if err := saveLedger(defaultLedgerPath(), ledger); err != nil {
		fatal(err.Error())
	}
	return code, payload
}

func printIssued(code string, payload licenseissuer.ActivationPayload) {
	fmt.Println()
	fmt.Println("许可证：", payload.LicenseID)
	fmt.Println("客户：", payload.Customer)
	fmt.Println("到期时间：", payload.ExpiresAt)
	fmt.Println("激活码：")
	fmt.Println(code)
}

func loadLedger(path string) (ledgerFile, error) {
	ledger := ledgerFile{Licenses: map[string]ledgerRecord{}}
	raw, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return ledger, nil
	}
	if err != nil {
		return ledger, err
	}
	if err := json.Unmarshal(raw, &ledger); err != nil {
		return ledger, fmt.Errorf("读取签发台账失败: %w", err)
	}
	if ledger.Licenses == nil {
		ledger.Licenses = map[string]ledgerRecord{}
	}
	return ledger, nil
}

func saveLedger(path string, ledger ledgerFile) error {
	raw, err := json.MarshalIndent(ledger, "", "  ")
	if err != nil {
		return err
	}
	tempPath := path + ".tmp"
	if err := os.WriteFile(tempPath, raw, 0o600); err != nil {
		return err
	}
	if err := os.Rename(tempPath, path); err != nil {
		_ = os.Remove(path)
		return os.Rename(tempPath, path)
	}
	return nil
}

func defaultKeyPath() string {
	exe, err := os.Executable()
	if err != nil {
		return "Laogu-License-Issuer.key"
	}
	return filepath.Join(filepath.Dir(exe), "Laogu-License-Issuer.key")
}

func defaultLedgerPath() string {
	exe, err := os.Executable()
	if err != nil {
		return "Laogu-License-Ledger.json"
	}
	return filepath.Join(filepath.Dir(exe), "Laogu-License-Ledger.json")
}

func clipboardSetText(value string) error {
	cmd := exec.Command("powershell.exe", "-NoProfile", "-NonInteractive", "-Command", "Set-Clipboard -Value $env:LAOGU_LICENSE_CODE")
	cmd.Env = append(os.Environ(), "LAOGU_LICENSE_CODE="+value)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	return cmd.Run()
}

func pause(reader *bufio.Reader) {
	fmt.Print("按回车退出……")
	_, _ = reader.ReadString('\n')
}

func fatal(message string) {
	fmt.Fprintln(os.Stderr, "错误：", message)
	os.Exit(1)
}
