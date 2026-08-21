//go:build !windows

package offlinelicense

import (
	"fmt"
	"os"
	"strings"
)

func machineFingerprint() (string, error) {
	hostname, err := os.Hostname()
	if err != nil || strings.TrimSpace(hostname) == "" {
		return "", fmt.Errorf("unable to identify this computer")
	}
	return strings.ToLower(strings.TrimSpace(hostname)), nil
}
