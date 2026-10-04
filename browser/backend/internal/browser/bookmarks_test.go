package browser

import (
	"ant-chrome/backend/internal/config"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestReplaceBookmarkURLUpdatesExistingBookmark(t *testing.T) {
	userDataDir := t.TempDir()
	oldURL := "ant://fingerprint-check"
	newURL := "file:///tmp/fingerprint-check/index.html?profileId=profile-123"

	if err := EnsureDefaultBookmarks(userDataDir, []config.BrowserBookmark{{Name: "指纹检测", URL: oldURL}}); err != nil {
		t.Fatalf("EnsureDefaultBookmarks error = %v", err)
	}
	changed, err := ReplaceBookmarkURL(userDataDir, oldURL, newURL)
	if err != nil {
		t.Fatalf("ReplaceBookmarkURL error = %v", err)
	}
	if !changed {
		t.Fatalf("changed = false, want true")
	}

	data, err := os.ReadFile(filepath.Join(userDataDir, "Default", "Bookmarks"))
	if err != nil {
		t.Fatalf("read bookmarks error = %v", err)
	}
	content := string(data)
	if strings.Contains(content, oldURL) {
		t.Fatalf("bookmarks still contains old URL: %s", content)
	}
	if !strings.Contains(content, newURL) {
		t.Fatalf("bookmarks missing new URL: %s", content)
	}
}

func TestRemoveExactBookmarksPreservesUserModifiedEntries(t *testing.T) {
	userDataDir := t.TempDir()
	retired := []config.BrowserBookmark{
		{Name: "IPPure", URL: "https://ippure.com/"},
		{Name: "IPLark", URL: "https://iplark.com/"},
		{Name: "Ping0", URL: "https://ping0.cc/"},
	}
	initial := append(append([]config.BrowserBookmark{}, retired...),
		config.BrowserBookmark{Name: "Google", URL: "https://www.google.com/"})
	if err := EnsureDefaultBookmarks(userDataDir, initial); err != nil {
		t.Fatalf("EnsureDefaultBookmarks error = %v", err)
	}

	bookmarksPath := filepath.Join(userDataDir, "Default", "Bookmarks")
	data, err := os.ReadFile(bookmarksPath)
	if err != nil {
		t.Fatalf("read bookmarks error = %v", err)
	}
	content := strings.Replace(string(data), `"name": "Ping0"`, `"name": "我的 Ping0"`, 1)
	if err := os.WriteFile(bookmarksPath, []byte(content), 0o644); err != nil {
		t.Fatalf("write bookmarks error = %v", err)
	}

	removed, err := RemoveExactBookmarks(userDataDir, retired)
	if err != nil {
		t.Fatalf("RemoveExactBookmarks error = %v", err)
	}
	if removed != 2 {
		t.Fatalf("removed = %d, want 2", removed)
	}
	data, err = os.ReadFile(bookmarksPath)
	if err != nil {
		t.Fatalf("read migrated bookmarks error = %v", err)
	}
	content = string(data)
	if strings.Contains(content, "https://ippure.com/") || strings.Contains(content, "https://iplark.com/") {
		t.Fatalf("retired defaults still present: %s", content)
	}
	if !strings.Contains(content, "https://ping0.cc/") || !strings.Contains(content, "我的 Ping0") {
		t.Fatalf("user-modified bookmark was removed: %s", content)
	}
	if !strings.Contains(content, "https://www.google.com/") {
		t.Fatalf("unrelated bookmark was removed: %s", content)
	}
}
