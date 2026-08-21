package backend

import (
	"fmt"
	"os"
	"strings"
	"time"

	"ant-chrome/backend/internal/apppath"
	"ant-chrome/backend/internal/offlinelicense"

	"github.com/wailsapp/wails/v2/pkg/runtime"
)

type LicenseStatus = offlinelicense.Status

func (a *App) ensureLicenseManager() error {
	a.licenseMu.Lock()
	defer a.licenseMu.Unlock()
	if a.licenseMgr != nil {
		return nil
	}
	if err := apppath.EnsureWritableLayout(a.appRoot); err != nil {
		return err
	}
	manager, err := offlinelicense.NewManager(a.appStateRootAbs(), a.appVersion(), offlineLicenseIssuerPublicKey)
	if err != nil {
		return err
	}
	a.licenseMgr = manager
	return nil
}

func (a *App) LicenseStatus() (LicenseStatus, error) {
	if err := a.ensureLicenseManager(); err != nil {
		return LicenseStatus{}, err
	}
	return a.licenseMgr.Status(), nil
}

func (a *App) LicenseRequestCode(regenerate bool) (LicenseStatus, error) {
	if err := a.ensureLicenseManager(); err != nil {
		return LicenseStatus{}, err
	}
	return a.licenseMgr.RequestCode(regenerate)
}

func (a *App) LicenseActivate(activationCode string) (LicenseStatus, error) {
	if strings.TrimSpace(activationCode) == "" {
		return LicenseStatus{}, fmt.Errorf("激活码不能为空")
	}
	if err := a.ensureLicenseManager(); err != nil {
		return LicenseStatus{}, err
	}
	status, err := a.licenseMgr.Activate(activationCode)
	if err != nil {
		return status, err
	}
	if status.Licensed && a.licenseServerURL() != "" {
		status, err = a.licenseMgr.RemoteCheck(a.licenseServerURL())
		if err != nil && !status.Licensed {
			return status, err
		}
	}
	if status.Licensed {
		a.startupLicensed(a.ctx)
	}
	return status, nil
}

// LicenseRemoteCheck performs an explicit online check.  The URL is supplied
// by the deployment rather than hard-coded into the browser binary.
func (a *App) LicenseRemoteCheck(serverURL string) (LicenseStatus, error) {
	if err := a.ensureLicenseManager(); err != nil {
		return LicenseStatus{}, err
	}
	return a.licenseMgr.RemoteCheck(serverURL)
}

func (a *App) licenseServerURL() string {
	if value := strings.TrimSpace(os.Getenv("LAOGU_LICENSE_SERVER_URL")); value != "" {
		return value
	}
	if a.config != nil {
		return strings.TrimSpace(a.config.License.ServerURL)
	}
	// License initialization happens before the rest of the runtime config is
	// loaded, so read the same config file once to make YAML-only deployments
	// work without requiring an environment variable.
	if cfg, err := LoadConfig(a.resolveAppPath("config.yaml")); err == nil && cfg != nil {
		return strings.TrimSpace(cfg.License.ServerURL)
	}
	return ""
}

func (a *App) startLicenseMonitor() {
	a.licenseMonitorOnce.Do(func() {
		go func() {
			ticker := time.NewTicker(time.Minute)
			defer ticker.Stop()
			for range ticker.C {
				if a.licenseMgr == nil {
					continue
				}
				if serverURL := a.licenseServerURL(); serverURL != "" {
					status, _ := a.licenseMgr.RemoteCheck(serverURL)
					if status.Licensed {
						continue
					}
				} else if a.licenseMgr.Status().Licensed {
					continue
				}
				a.setQuitMode(quitModeFull)
				a.stopRuntimeServices()
				if a.ctx != nil {
					runtime.Quit(a.ctx)
				}
				return
			}
		}()
	})
}
