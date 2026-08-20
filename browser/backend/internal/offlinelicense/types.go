package offlinelicense

const (
	RequestPrefix    = "LGREQ1."
	ActivationPrefix = "LGACT1."
)

type RequestPayload struct {
	Version          int    `json:"version"`
	DeviceID         string `json:"deviceId"`
	InstallPublicKey string `json:"installPublicKey"`
	Nonce            string `json:"nonce"`
	AppVersion       string `json:"appVersion"`
	RequestedAt      string `json:"requestedAt"`
}

type ActivationPayload struct {
	Version          int      `json:"version"`
	LicenseID        string   `json:"licenseId"`
	Customer         string   `json:"customer"`
	DeviceID         string   `json:"deviceId"`
	InstallPublicKey string   `json:"installPublicKey"`
	RequestNonce     string   `json:"requestNonce"`
	IssuedAt         string   `json:"issuedAt"`
	ExpiresAt        string   `json:"expiresAt"`
	Features         []string `json:"features"`
}

type Status struct {
	Licensed          bool     `json:"licensed"`
	State             string   `json:"state"`
	Message           string   `json:"message"`
	DeviceID          string   `json:"deviceId"`
	RequestCode       string   `json:"requestCode"`
	LicenseID         string   `json:"licenseId"`
	Customer          string   `json:"customer"`
	IssuedAt          string   `json:"issuedAt"`
	ExpiresAt         string   `json:"expiresAt"`
	RemainingDays     int      `json:"remainingDays"`
	Features          []string `json:"features"`
	ClockRollback     bool     `json:"clockRollback"`
	RemoteState       string   `json:"remoteState"`
	LastOnlineCheckAt string   `json:"lastOnlineCheckAt"`
	OfflineGraceDays  int      `json:"offlineGraceDays"`
}

type stateFile struct {
	InstallPrivateKey string `json:"installPrivateKey"`
	InstallPublicKey  string `json:"installPublicKey"`
	DeviceID          string `json:"deviceId"`
	PendingNonce      string `json:"pendingNonce"`
	PendingRequest    string `json:"pendingRequest"`
	ActivationCode    string `json:"activationCode"`
	LastSeenAt        string `json:"lastSeenAt"`
	LastOnlineCheckAt string `json:"lastOnlineCheckAt"`
	OfflineGraceDays  int    `json:"offlineGraceDays"`
}

type IssueOptions struct {
	LicenseID string
	Customer  string
	Days      int
	Features  []string
}
