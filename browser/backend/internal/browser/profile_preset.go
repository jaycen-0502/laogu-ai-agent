package browser

import (
	"encoding/json"
	"fmt"
	"math/rand"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"time"
)

// RegionPreset 包含地区环境专属配置
type RegionPreset struct {
	CountryName string   // 地区名称 (如 "美国", "日本")
	Languages   []string // 浏览器 Accept-Language
	Timezone    string   // 时区 (如 America/New_York)
	Latitude    float64  // 示例坐标 纬度
	Longitude   float64  // 示例坐标 经度
	UserAgents  []string // 常用桌面 User-Agent 库
}

// 预置地区模版
var regionPresets = map[string]RegionPreset{
	"US": {
		CountryName: "美国",
		Languages:   []string{"en-US,en;q=0.9"},
		Timezone:    "America/New_York",
		Latitude:    40.7128,
		Longitude:   -74.0060,
		UserAgents: []string{
			"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
			"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
			"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
		},
	},
	"JP": {
		CountryName: "日本",
		Languages:   []string{"ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7"},
		Timezone:    "Asia/Tokyo",
		Latitude:    35.6762,
		Longitude:   139.6503,
		UserAgents: []string{
			"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
			"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
			"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
		},
	},
}

// 通用 User-Agent 库 (用于动态代理识别)
var defaultUserAgents = []string{
	"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
	"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
	"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
}

// 国家代码 -> Language 标头映射
var countryLanguageMap = map[string]string{
	"JP": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
	"US": "en-US,en;q=0.9",
	"CN": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
	"GB": "en-GB,en;q=0.9,en-US;q=0.8",
	"KR": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
	"DE": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
}

// IPInfo 存储从代理节点解析到的 IP 地理元数据
type IPInfo struct {
	Status      string  `json:"status"`
	CountryCode string  `json:"countryCode"`
	Timezone    string  `json:"timezone"`
	Lat         float64 `json:"lat"`
	Lon         float64 `json:"lon"`
}

// FetchIPInfoViaProxy 通过指定代理访问 IP 接口获取地理与时区数据
func FetchIPInfoViaProxy(proxyURLStr string) (*IPInfo, error) {
	client := &http.Client{Timeout: 10 * time.Second}

	if proxyURLStr != "" {
		proxyURL, err := url.Parse(proxyURLStr)
		if err != nil {
			return nil, fmt.Errorf("代理 URL 格式错误: %v", err)
		}
		client.Transport = &http.Transport{
			Proxy: http.ProxyURL(proxyURL),
		}
	}

	resp, err := client.Get("http://ip-api.com/json/?fields=status,countryCode,timezone,lat,lon")
	if err != nil {
		return nil, fmt.Errorf("通过代理拉取 IP 信息失败: %v", err)
	}
	defer resp.Body.Close()

	var info IPInfo
	if err := json.NewDecoder(resp.Body).Decode(&info); err != nil {
		return nil, fmt.Errorf("解析 IP 元数据失败: %v", err)
	}

	if info.Status != "success" {
		return nil, fmt.Errorf("IP 查询接口返回失败状态")
	}

	return &info, nil
}

// getAutoExtensionFlags 自动检测并返回挂载 ScriptCat 等扩展程序的 Flag
func getAutoExtensionFlags() []string {
	var extFlags []string
	execDir, err := os.Getwd()
	if err != nil {
		return extFlags
	}

	// 优先寻找 ./extensions/scriptcat，其次寻找根目录下的 ./scriptcat
	possiblePaths := []string{
		filepath.Join(execDir, "extensions", "scriptcat"),
		filepath.Join(execDir, "scriptcat"),
	}

	var validExtPaths []string
	for _, p := range possiblePaths {
		if info, err := os.Stat(p); err == nil && info.IsDir() {
			validExtPaths = append(validExtPaths, p)
			break // 匹配到一个即可
		}
	}

	if len(validExtPaths) > 0 {
		extPathStr := validExtPaths[0]
		extFlags = append(extFlags,
			fmt.Sprintf("--disable-extensions-except=%s", extPathStr),
			fmt.Sprintf("--load-extension=%s", extPathStr),
		)
	}

	return extFlags
}

// GeneratePresetLaunchArgs 一键生成指定固定地区且带【随机参数扰动】的启动 Flag 列表
func GeneratePresetLaunchArgs(region string) ([]string, error) {
	preset, exists := regionPresets[region]
	if !exists {
		return nil, fmt.Errorf("未知的地区预置类型: %s", region)
	}

	r := rand.New(rand.NewSource(time.Now().UnixNano()))

	// 1. 随机选取 User-Agent
	selectedUA := preset.UserAgents[r.Intn(len(preset.UserAgents))]

	// 2. 随机选取 CPU 核心数 (4, 8, 12, 16) 与内存 (8GB, 16GB, 32GB)
	cpuCores := []int{4, 8, 12, 16}[r.Intn(4)]
	memoryGB := []int{8, 16, 32}[r.Intn(3)]

	// 3. 对经纬度施加微小随机偏移 (模拟同城不同街道)
	latOffset := (r.Float64() - 0.5) * 0.1
	lngOffset := (r.Float64() - 0.5) * 0.1
	realLat := preset.Latitude + latOffset
	realLng := preset.Longitude + lngOffset

	// 4. 生成独特的 Canvas/ClientRects 噪音 Seed
	randomNoiseSeed := r.Int63()

	args := []string{
		fmt.Sprintf("--tz=%s", preset.Timezone),
		fmt.Sprintf("--lang=%s", preset.Languages[0]),
		fmt.Sprintf("--accept-lang=%s", preset.Languages[0]),

		fmt.Sprintf("--user-agent=%s", selectedUA),
		fmt.Sprintf("--js-flags=--max-old-space-size=%d", memoryGB*1024),

		fmt.Sprintf("--fingerprint-canvas-seed=%d", randomNoiseSeed),
		fmt.Sprintf("--fingerprint-hardware-concurrency=%d", cpuCores),

		fmt.Sprintf("--geolocation-latitude=%.6f", realLat),
		fmt.Sprintf("--geolocation-longitude=%.6f", realLng),

		// 【关键添加】跨设备解密与崩溃恢复 Flag
		"--password-store=basic",
		"--use-mock-keychain",
		"--hide-crash-restore-bubble",
	}

	// 5. 自动追加动态扩展挂载 Flag（解决跨电脑丢失插件问题）
	args = append(args, getAutoExtensionFlags()...)

	return EnsureRuntimeFingerprintArgs(args), nil
}

// GenerateDynamicPresetArgs 根据输入的代理 IP 自动匹配时区/语言/坐标，硬件与指纹 Seed 保持全随机
func GenerateDynamicPresetArgs(proxyURL string) ([]string, int64, error) {
	r := rand.New(rand.NewSource(time.Now().UnixNano()))

	// 1. 优先查代理 IP 的真正归属地
	ipInfo, err := FetchIPInfoViaProxy(proxyURL)
	if err != nil {
		// 降级预案：若代理未能联网/不通，默认兜底使用美国纽约参数，保证服务不中断
		ipInfo = &IPInfo{
			CountryCode: "US",
			Timezone:    "America/New_York",
			Lat:         40.7128,
			Lon:         -74.0060,
		}
	}

	// 2. 硬件参数全随机 (CPU, 内存, UA, Canvas Seed)
	selectedUA := defaultUserAgents[r.Intn(len(defaultUserAgents))]
	cpuCores := []int{4, 8, 12, 16}[r.Intn(4)]
	memoryGB := []int{8, 16, 32}[r.Intn(3)]
	randomNoiseSeed := r.Int63()

	// 3. 动态坐标抖动 Jitter
	latOffset := (r.Float64() - 0.5) * 0.1
	lngOffset := (r.Float64() - 0.5) * 0.1
	realLat := ipInfo.Lat + latOffset
	realLng := ipInfo.Lon + lngOffset

	// 4. 获取代理对应国家的语言 Header
	langHeader, exists := countryLanguageMap[ipInfo.CountryCode]
	if !exists {
		langHeader = "en-US,en;q=0.9"
	}

	// 5. 组合 Flags
	args := []string{
		fmt.Sprintf("--tz=%s", ipInfo.Timezone),
		fmt.Sprintf("--lang=%s", ipInfo.CountryCode),
		fmt.Sprintf("--accept-lang=%s", langHeader),

		fmt.Sprintf("--user-agent=%s", selectedUA),
		fmt.Sprintf("--js-flags=--max-old-space-size=%d", memoryGB*1024),

		fmt.Sprintf("--fingerprint-canvas-seed=%d", randomNoiseSeed),
		fmt.Sprintf("--fingerprint-hardware-concurrency=%d", cpuCores),

		fmt.Sprintf("--geolocation-latitude=%.6f", realLat),
		fmt.Sprintf("--geolocation-longitude=%.6f", realLng),

		// 【关键添加】跨设备解密与崩溃恢复 Flag
		"--password-store=basic",
		"--use-mock-keychain",
		"--hide-crash-restore-bubble",
	}

	if proxyURL != "" {
		args = append(args, fmt.Sprintf("--proxy-server=%s", proxyURL))
	}

	// 6. 自动追加动态扩展挂载 Flag（解决跨电脑丢失插件问题）
	args = append(args, getAutoExtensionFlags()...)

	return EnsureRuntimeFingerprintArgs(args), randomNoiseSeed, nil
}
