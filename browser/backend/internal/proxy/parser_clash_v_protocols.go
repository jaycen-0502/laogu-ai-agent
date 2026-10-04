package proxy

import (
	"fmt"
	"strings"
)

func buildOutboundFromClashVless(node map[string]interface{}) (map[string]interface{}, string, error) {
	host := getMapString(node, "server")
	port := getMapInt(node, "port")
	id := getMapString(node, "uuid")
	flow := getMapString(node, "flow")
	sni := getMapString(node, "sni")
	if sni == "" {
		sni = getMapString(node, "servername")
	}
	network := getMapString(node, "network")
	out := map[string]interface{}{
		"protocol": "vless",
		"tag":      "proxy-out",
		"settings": map[string]interface{}{
			"vnext": []interface{}{
				map[string]interface{}{
					"address": host,
					"port":    port,
					"users": []interface{}{
						map[string]interface{}{
							"id":         id,
							"flow":       flow,
							"encryption": "none",
						},
					},
				},
			},
		},
	}
	stream := map[string]interface{}{}
	tlsVal := strings.ToLower(getMapString(node, "tls"))
	realityRaw, hasRealityOpts := node["reality-opts"]
	if !hasRealityOpts {
		realityRaw, hasRealityOpts = node["realityOpts"]
	}

	if hasRealityOpts {
		stream["network"] = "tcp"
		realityOpts := map[string]interface{}{
			"spiderX": "",
		}
		if sni != "" {
			realityOpts["serverName"] = sni
		}
		fingerprint := firstMapString(node, "client-fingerprint", "clientFingerprint", "fingerprint")
		if fingerprint == "" {
			fingerprint = "chrome"
		}
		realityOpts["fingerprint"] = fingerprint
		realityMap := toStringMap(realityRaw)
		publicKey := firstMapString(realityMap, "public-key", "publicKey", "public_key", "pbk")
		shortID := firstMapString(realityMap, "short-id", "shortId", "short_id", "sid")
		spiderX := firstMapString(realityMap, "spider-x", "spiderX", "spx")

		// A common hand-written YAML mistake leaves `reality-opts:` empty and
		// places its fields at the node level. Recover those fields so an otherwise
		// valid subscription does not silently turn into a broken Xray outbound.
		if publicKey == "" {
			publicKey = firstMapString(node, "public-key", "publicKey", "public_key", "pbk")
		}
		if shortID == "" {
			shortID = firstMapString(node, "short-id", "shortId", "short_id", "sid")
		}
		if spiderX == "" {
			spiderX = firstMapString(node, "spider-x", "spiderX", "spx")
		}
		if publicKey == "" {
			return nil, "", fmt.Errorf("VLESS REALITY 配置缺少 reality-opts.public-key；请确认 public-key 和 short-id 缩进在 reality-opts 下")
		}
		realityOpts["publicKey"] = publicKey
		if shortID != "" {
			realityOpts["shortId"] = shortID
		}
		if spiderX != "" {
			realityOpts["spiderX"] = spiderX
		}
		stream["security"] = "reality"
		stream["realitySettings"] = realityOpts
	} else if getMapBool(node, "tls") || tlsVal == "true" || tlsVal == "tls" {
		tlsSettings := map[string]interface{}{}
		if sni != "" {
			tlsSettings["serverName"] = sni
		}
		applyClashTLSClientOptions(node, tlsSettings)
		stream["security"] = "tls"
		stream["tlsSettings"] = tlsSettings
	}
	if network == "ws" {
		stream["network"] = "ws"
		stream["wsSettings"] = buildClashWSSettings(node)
	}
	if network == "grpc" {
		stream["network"] = "grpc"
		if grpc := buildClashGRPCSettings(node); len(grpc) > 0 {
			stream["grpcSettings"] = grpc
		}
	}
	if len(stream) > 0 {
		out["streamSettings"] = stream
	}
	applyXrayBrowserOutboundTuning(node, out)
	return out, "", nil
}

func firstMapString(m map[string]interface{}, keys ...string) string {
	if m == nil {
		return ""
	}
	for _, key := range keys {
		if value := getMapString(m, key); value != "" {
			return value
		}
	}
	return ""
}

func buildOutboundFromClashVmess(node map[string]interface{}) (map[string]interface{}, string, error) {
	host := getMapString(node, "server")
	port := getMapInt(node, "port")
	id := getMapString(node, "uuid")
	cipher := getMapString(node, "cipher")
	if cipher == "" {
		cipher = "auto"
	}
	network := getMapString(node, "network")
	sni := getMapString(node, "sni")
	if sni == "" {
		sni = getMapString(node, "servername")
	}
	out := map[string]interface{}{
		"protocol": "vmess",
		"tag":      "proxy-out",
		"settings": map[string]interface{}{
			"vnext": []interface{}{
				map[string]interface{}{
					"address": host,
					"port":    port,
					"users": []interface{}{
						map[string]interface{}{
							"id":       id,
							"security": cipher,
						},
					},
				},
			},
		},
	}
	stream := map[string]interface{}{}
	if getMapBool(node, "tls") || strings.ToLower(getMapString(node, "tls")) == "true" {
		tlsSettings := map[string]interface{}{}
		if sni != "" {
			tlsSettings["serverName"] = sni
		}
		applyClashTLSClientOptions(node, tlsSettings)
		stream["security"] = "tls"
		stream["tlsSettings"] = tlsSettings
	}
	if network == "ws" {
		stream["network"] = "ws"
		stream["wsSettings"] = buildClashWSSettings(node)
	}
	if network == "grpc" {
		stream["network"] = "grpc"
		if grpc := buildClashGRPCSettings(node); len(grpc) > 0 {
			stream["grpcSettings"] = grpc
		}
	}
	if len(stream) > 0 {
		out["streamSettings"] = stream
	}
	applyXrayBrowserOutboundTuning(node, out)
	return out, "", nil
}
