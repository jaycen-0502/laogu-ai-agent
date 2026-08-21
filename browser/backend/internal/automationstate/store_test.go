package automationstate

import (
	"fmt"
	"path/filepath"
	"sync"
	"testing"

	"ant-chrome/backend/internal/database"
)

func newTestStore(t *testing.T) (*Store, *database.DB) {
	t.Helper()
	db, err := database.NewDB(filepath.Join(t.TempDir(), "automation-state.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = db.Close() })
	if err := db.Migrate(); err != nil {
		t.Fatal(err)
	}
	for _, profileID := range []string{"profile-a", "profile-b"} {
		_, err := db.GetConn().Exec(`
			INSERT INTO browser_profiles (profile_id, profile_name, created_at, updated_at)
			VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)`, profileID, profileID)
		if err != nil {
			t.Fatal(err)
		}
	}
	return NewStore(db.GetConn()), db
}

func TestKeywordsAndCursorPersistIndependently(t *testing.T) {
	store, _ := newTestStore(t)

	state, err := store.SaveKeywords(" profile-a ", " X ", "script-1", []string{" AI ", "ai", "Playwright", ""})
	if err != nil {
		t.Fatal(err)
	}
	if got, want := fmt.Sprint(state.Keywords), "[AI Playwright]"; got != want {
		t.Fatalf("keywords = %s, want %s", got, want)
	}

	state, err = store.SaveCursor("profile-a", "x", "script-1", map[string]any{"page": 3.0})
	if err != nil {
		t.Fatal(err)
	}
	if got, want := fmt.Sprint(state.Keywords), "[AI Playwright]"; got != want {
		t.Fatalf("keywords after cursor save = %s, want %s", got, want)
	}
	if got := state.Cursor["page"]; got != float64(3) {
		t.Fatalf("cursor page = %#v, want 3", got)
	}
}

func TestProcessedItemsAreIsolated(t *testing.T) {
	store, _ := newTestStore(t)
	input := ProcessedItemInput{ProfileID: "profile-a", Platform: "x", ItemType: "user", ItemKey: "123"}
	if _, err := store.MarkProcessed(input); err != nil {
		t.Fatal(err)
	}

	for _, tc := range []struct {
		profileID string
		platform  string
		want      bool
	}{
		{"profile-a", "x", true},
		{"profile-b", "x", false},
		{"profile-a", "linkedin", false},
	} {
		status, err := store.IsProcessed(ProcessedItemInput{
			ProfileID: tc.profileID, Platform: tc.platform, ItemType: "user", ItemKey: "123",
		})
		if err != nil {
			t.Fatal(err)
		}
		if status.Processed != tc.want {
			t.Fatalf("processed(%s, %s) = %v, want %v", tc.profileID, tc.platform, status.Processed, tc.want)
		}
	}
}

func TestCountersIncrementAtomicallyAndAreIsolated(t *testing.T) {
	store, _ := newTestStore(t)
	const increments = 20
	var wg sync.WaitGroup
	errCh := make(chan error, increments)
	for i := 0; i < increments; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_, err := store.IncrementCounter(CounterIncrementInput{
				ProfileID: "profile-a", Platform: "x", CounterDate: "2026-08-14", CounterKey: "reviewed",
			})
			errCh <- err
		}()
	}
	wg.Wait()
	close(errCh)
	for err := range errCh {
		if err != nil {
			t.Fatal(err)
		}
	}

	counters, err := store.ListCounters("profile-a", "x", "2026-08-14")
	if err != nil {
		t.Fatal(err)
	}
	if len(counters) != 1 || counters[0].Value != increments {
		t.Fatalf("counters = %#v, want value %d", counters, increments)
	}
	other, err := store.ListCounters("profile-b", "x", "2026-08-14")
	if err != nil {
		t.Fatal(err)
	}
	if len(other) != 0 {
		t.Fatalf("other profile counters = %#v, want empty", other)
	}
}

func TestProfileDeleteCascadesAutomationState(t *testing.T) {
	store, db := newTestStore(t)
	if _, err := store.SaveKeywords("profile-a", "x", "script-1", []string{"AI"}); err != nil {
		t.Fatal(err)
	}
	if _, err := store.MarkProcessed(ProcessedItemInput{ProfileID: "profile-a", Platform: "x", ItemKey: "123"}); err != nil {
		t.Fatal(err)
	}
	if _, err := store.IncrementCounter(CounterIncrementInput{ProfileID: "profile-a", Platform: "x", CounterKey: "reviewed"}); err != nil {
		t.Fatal(err)
	}
	if _, err := db.GetConn().Exec(`DELETE FROM browser_profiles WHERE profile_id = ?`, "profile-a"); err != nil {
		t.Fatal(err)
	}

	for _, table := range []string{"automation_account_states", "automation_processed_items", "automation_daily_counters"} {
		var count int
		if err := db.GetConn().QueryRow("SELECT COUNT(1) FROM "+table+" WHERE profile_id = ?", "profile-a").Scan(&count); err != nil {
			t.Fatal(err)
		}
		if count != 0 {
			t.Fatalf("%s retained %d rows after profile deletion", table, count)
		}
	}
}
