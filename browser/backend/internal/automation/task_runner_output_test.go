package automation

import "testing"

func TestParseTaskRunnerResponseAcceptsCleanJSON(t *testing.T) {
	response, normalized, err := parseTaskRunnerResponse([]byte(`{"ok":true,"summary":"done"}`))
	if err != nil {
		t.Fatal(err)
	}
	if !response.OK || response.Summary != "done" {
		t.Fatalf("unexpected response: %+v", response)
	}
	if string(normalized) != `{"ok":true,"summary":"done"}` {
		t.Fatalf("normalized output = %q", normalized)
	}
}

func TestParseTaskRunnerResponseSkipsBOMAndMixedConsoleOutput(t *testing.T) {
	output := []byte("\xef\xbb\xbfâ dependency warning\r\nconsole message\r\n{\"ok\":true,\"summary\":\"完成\"}\r\ntrailing notice\r\n")
	response, normalized, err := parseTaskRunnerResponse(output)
	if err != nil {
		t.Fatal(err)
	}
	if !response.OK || response.Summary != "完成" {
		t.Fatalf("unexpected response: %+v", response)
	}
	if string(normalized) != `{"ok":true,"summary":"完成"}` {
		t.Fatalf("normalized output = %q", normalized)
	}
}

func TestParseTaskRunnerResponseRejectsMissingJSON(t *testing.T) {
	if _, _, err := parseTaskRunnerResponse([]byte("â warning only")); err == nil {
		t.Fatal("expected missing JSON to fail")
	}
}
