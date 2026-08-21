//go:build !windows

package offlinelicense

func protectData(data []byte) ([]byte, error) {
	return append([]byte(nil), data...), nil
}

func unprotectData(data []byte) ([]byte, error) {
	return append([]byte(nil), data...), nil
}
