package backend

import "testing"

func TestAutomationSelectorParamsByCodeAndMergeAreIsolated(t *testing.T) {
	selector := map[string]any{
		"profileParamsByCode": map[string]any{
			" buyer_001 ": map[string]any{
				"keyword":  "OpenAI",
				"keywords": []any{"OpenAI", "Agent"},
			},
			"BUYER_002": map[string]any{
				"keyword": "游戏",
			},
		},
	}

	byCode := automationSelectorParamsByCode(selector)
	if len(byCode) != 2 {
		t.Fatalf("params by code length = %d, want 2", len(byCode))
	}
	first := mergeAutomationParams(map[string]any{"timeoutMs": 30000}, byCode["BUYER_001"])
	second := mergeAutomationParams(map[string]any{"timeoutMs": 30000}, byCode["BUYER_002"])

	if first["keyword"] != "OpenAI" || second["keyword"] != "游戏" {
		t.Fatalf("unexpected merged params: first=%#v second=%#v", first, second)
	}
	first["keyword"] = "changed"
	if second["keyword"] != "游戏" {
		t.Fatalf("mutating first params affected second params: %#v", second)
	}
}
