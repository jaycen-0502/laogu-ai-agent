//go:build windows

package offlinelicense

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"syscall"
	"time"

	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/registry"
)

func machineFingerprint() (string, error) {
	machineGUID := ""
	key, err := registry.OpenKey(registry.LOCAL_MACHINE, `SOFTWARE\Microsoft\Cryptography`, registry.QUERY_VALUE|registry.WOW64_64KEY)
	if err == nil {
		machineGUID, _, _ = key.GetStringValue("MachineGuid")
		_ = key.Close()
	}
	if strings.TrimSpace(machineGUID) == "" {
		return "", fmt.Errorf("无法读取 Windows MachineGuid")
	}
	root := os.Getenv("SystemDrive")
	if root == "" {
		root = "C:"
	}
	root += `\`
	rootPtr, _ := windows.UTF16PtrFromString(root)
	var serial uint32
	_ = windows.GetVolumeInformation(rootPtr, nil, 0, &serial, nil, nil, nil, 0)
	return strings.ToLower(strings.TrimSpace(machineGUID)) + "|" + fmt.Sprintf("%08x", serial) + "|" + readSystemUUID(), nil
}

func readSystemUUID() string {
	ctx, cancel := context.WithTimeout(context.Background(), 4*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, "powershell.exe", "-NoProfile", "-NonInteractive", "-Command", "(Get-CimInstance Win32_ComputerSystemProduct).UUID")
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	raw, err := cmd.Output()
	if err != nil {
		return ""
	}
	return strings.ToLower(strings.TrimSpace(string(raw)))
}
