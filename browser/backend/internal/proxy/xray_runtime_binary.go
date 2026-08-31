package proxy

import (
	"ant-chrome/backend/internal/fsutil"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	goruntime "runtime"
	"strings"
)

// resolveBinary locates a user-selected Xray executable, accepting either a
// file path or a directory containing xray.exe/xray.
func (m *XrayManager) resolveBinary() (string, error) {
	binaryNames := []string{"xray"}
	if goruntime.GOOS == "windows" {
		binaryNames = []string{"xray.exe", "xray"}
	}
	platformDir := fmt.Sprintf("%s-%s", goruntime.GOOS, goruntime.GOARCH)

	configured := strings.TrimSpace(m.Config.Browser.XrayBinaryPath)
	if configured != "" {
		if candidate, ok, err := resolveXrayCandidate(resolveEnvPath(configured, m.AppRoot), binaryNames); err != nil {
			return "", err
		} else if ok {
			return candidate, nil
		}
	}

	if env := strings.TrimSpace(os.Getenv("XRAY_BINARY_PATH")); env != "" {
		if candidate, ok, err := resolveXrayCandidate(env, binaryNames); err != nil {
			return "", err
		} else if ok {
			return candidate, nil
		}
	}

	searchDirs := make([]string, 0, 4)
	if m.AppRoot != "" {
		searchDirs = append(searchDirs,
			filepath.Join(m.AppRoot, "bin", platformDir),
			filepath.Join(m.AppRoot, "bin"),
		)
	}
	if exePath, err := os.Executable(); err == nil {
		exeDir := filepath.Dir(exePath)
		searchDirs = append(searchDirs,
			filepath.Join(exeDir, "bin", platformDir),
			filepath.Join(exeDir, "bin"),
		)
	}

	for _, dir := range searchDirs {
		if candidate, ok, err := resolveXrayCandidate(dir, binaryNames); err != nil {
			return "", err
		} else if ok {
			return candidate, nil
		}
	}

	for _, name := range binaryNames {
		if path, err := exec.LookPath(name); err == nil {
			if err := fsutil.EnsureExecutable(path); err != nil {
				return "", fmt.Errorf("xray executable is not runnable: %s: %w", path, err)
			}
			return path, nil
		}
	}

	return "", fmt.Errorf("xray executable not found; put xray in bin/%s/ or bin/, or set XrayBinaryPath", platformDir)
}

func resolveXrayCandidate(path string, binaryNames []string) (string, bool, error) {
	path = strings.TrimSpace(path)
	if path == "" {
		return "", false, nil
	}
	info, err := os.Stat(path)
	if err != nil {
		return "", false, nil
	}

	candidates := []string{path}
	if info.IsDir() {
		candidates = make([]string, 0, len(binaryNames))
		for _, name := range binaryNames {
			candidates = append(candidates, filepath.Join(path, name))
		}
	}
	for _, candidate := range candidates {
		candidateInfo, statErr := os.Stat(candidate)
		if statErr != nil || candidateInfo.IsDir() {
			continue
		}
		if err := fsutil.EnsureExecutable(candidate); err != nil {
			return "", false, fmt.Errorf("xray executable is not runnable: %s: %w", candidate, err)
		}
		return candidate, true, nil
	}
	return "", false, nil
}
