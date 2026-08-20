package licenseissuer

import (
	"crypto/ed25519"
	"time"

	"ant-chrome/backend/internal/offlinelicense"
)

type RequestPayload = offlinelicense.RequestPayload
type ActivationPayload = offlinelicense.ActivationPayload
type IssueOptions = offlinelicense.IssueOptions

func GenerateIssuerKeyFile(path string) (string, error) {
	return offlinelicense.GenerateIssuerKeyFile(path)
}

func LoadIssuerPrivateKey(path string) (ed25519.PrivateKey, error) {
	return offlinelicense.LoadIssuerPrivateKey(path)
}

func DecodeRequest(code string) (RequestPayload, error) {
	return offlinelicense.DecodeRequest(code)
}

func IssueActivation(requestCode string, privateKey ed25519.PrivateKey, options IssueOptions, now time.Time) (string, ActivationPayload, error) {
	return offlinelicense.IssueActivation(requestCode, privateKey, options, now)
}
