package automationstate

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"strings"
	"time"
)

type Store struct {
	db *sql.DB
}

type AccountState struct {
	ProfileID string         `json:"profileId"`
	Platform  string         `json:"platform"`
	ScriptID  string         `json:"scriptId"`
	Keywords  []string       `json:"keywords"`
	Cursor    map[string]any `json:"cursor"`
	CreatedAt string         `json:"createdAt"`
	UpdatedAt string         `json:"updatedAt"`
}

type ProcessedItemInput struct {
	ProfileID string         `json:"profileId"`
	Platform  string         `json:"platform"`
	ItemType  string         `json:"itemType"`
	ItemKey   string         `json:"itemKey"`
	Metadata  map[string]any `json:"metadata"`
}

type ProcessedItemStatus struct {
	Processed   bool   `json:"processed"`
	ProcessedAt string `json:"processedAt"`
}

type CounterIncrementInput struct {
	ProfileID   string `json:"profileId"`
	Platform    string `json:"platform"`
	CounterDate string `json:"counterDate"`
	CounterKey  string `json:"counterKey"`
	Delta       int64  `json:"delta"`
}

type DailyCounter struct {
	ProfileID   string `json:"profileId"`
	Platform    string `json:"platform"`
	CounterDate string `json:"counterDate"`
	CounterKey  string `json:"counterKey"`
	Value       int64  `json:"value"`
	UpdatedAt   string `json:"updatedAt"`
}

func NewStore(db *sql.DB) *Store {
	return &Store{db: db}
}

func normalizeScope(profileID, platform, scriptID string) (string, string, string, error) {
	profileID = strings.TrimSpace(profileID)
	if profileID == "" {
		return "", "", "", fmt.Errorf("profileId is required")
	}
	platform = strings.ToLower(strings.TrimSpace(platform))
	if platform == "" {
		platform = "generic"
	}
	scriptID = strings.TrimSpace(scriptID)
	if scriptID == "" {
		scriptID = "default"
	}
	return profileID, platform, scriptID, nil
}

func normalizeKeywords(values []string) []string {
	result := make([]string, 0, len(values))
	seen := make(map[string]struct{}, len(values))
	for _, value := range values {
		keyword := strings.TrimSpace(value)
		key := strings.ToLower(keyword)
		if keyword == "" {
			continue
		}
		if _, exists := seen[key]; exists {
			continue
		}
		seen[key] = struct{}{}
		result = append(result, keyword)
	}
	return result
}

func (s *Store) GetState(profileID, platform, scriptID string) (AccountState, error) {
	profileID, platform, scriptID, err := normalizeScope(profileID, platform, scriptID)
	if err != nil {
		return AccountState{}, err
	}
	state := AccountState{
		ProfileID: profileID,
		Platform:  platform,
		ScriptID:  scriptID,
		Keywords:  []string{},
		Cursor:    map[string]any{},
	}
	var keywordsJSON, cursorJSON string
	err = s.db.QueryRow(`
		SELECT keywords_json, cursor_json, created_at, updated_at
		FROM automation_account_states
		WHERE profile_id = ? AND platform = ? AND script_id = ?`,
		profileID, platform, scriptID,
	).Scan(&keywordsJSON, &cursorJSON, &state.CreatedAt, &state.UpdatedAt)
	if err == sql.ErrNoRows {
		return state, nil
	}
	if err != nil {
		return AccountState{}, fmt.Errorf("query automation account state: %w", err)
	}
	_ = json.Unmarshal([]byte(keywordsJSON), &state.Keywords)
	_ = json.Unmarshal([]byte(cursorJSON), &state.Cursor)
	if state.Keywords == nil {
		state.Keywords = []string{}
	}
	if state.Cursor == nil {
		state.Cursor = map[string]any{}
	}
	return state, nil
}

func (s *Store) SaveKeywords(profileID, platform, scriptID string, keywords []string) (AccountState, error) {
	profileID, platform, scriptID, err := normalizeScope(profileID, platform, scriptID)
	if err != nil {
		return AccountState{}, err
	}
	keywords = normalizeKeywords(keywords)
	keywordsJSON, _ := json.Marshal(keywords)
	now := time.Now().Format(time.RFC3339)
	_, err = s.db.Exec(`
		INSERT INTO automation_account_states
			(profile_id, platform, script_id, keywords_json, cursor_json, created_at, updated_at)
		VALUES (?, ?, ?, ?, '{}', ?, ?)
		ON CONFLICT(profile_id, platform, script_id) DO UPDATE SET
			keywords_json = excluded.keywords_json,
			updated_at = excluded.updated_at`,
		profileID, platform, scriptID, string(keywordsJSON), now, now,
	)
	if err != nil {
		return AccountState{}, fmt.Errorf("save automation keywords: %w", err)
	}
	return s.GetState(profileID, platform, scriptID)
}

func (s *Store) SaveCursor(profileID, platform, scriptID string, cursor map[string]any) (AccountState, error) {
	profileID, platform, scriptID, err := normalizeScope(profileID, platform, scriptID)
	if err != nil {
		return AccountState{}, err
	}
	if cursor == nil {
		cursor = map[string]any{}
	}
	cursorJSON, err := json.Marshal(cursor)
	if err != nil {
		return AccountState{}, fmt.Errorf("marshal automation cursor: %w", err)
	}
	now := time.Now().Format(time.RFC3339)
	_, err = s.db.Exec(`
		INSERT INTO automation_account_states
			(profile_id, platform, script_id, keywords_json, cursor_json, created_at, updated_at)
		VALUES (?, ?, ?, '[]', ?, ?, ?)
		ON CONFLICT(profile_id, platform, script_id) DO UPDATE SET
			cursor_json = excluded.cursor_json,
			updated_at = excluded.updated_at`,
		profileID, platform, scriptID, string(cursorJSON), now, now,
	)
	if err != nil {
		return AccountState{}, fmt.Errorf("save automation cursor: %w", err)
	}
	return s.GetState(profileID, platform, scriptID)
}

func normalizeProcessedInput(input ProcessedItemInput) (ProcessedItemInput, error) {
	input.ProfileID = strings.TrimSpace(input.ProfileID)
	input.Platform = strings.ToLower(strings.TrimSpace(input.Platform))
	if input.Platform == "" {
		input.Platform = "generic"
	}
	input.ItemType = strings.ToLower(strings.TrimSpace(input.ItemType))
	if input.ItemType == "" {
		input.ItemType = "item"
	}
	input.ItemKey = strings.TrimSpace(input.ItemKey)
	if input.ProfileID == "" || input.ItemKey == "" {
		return input, fmt.Errorf("profileId and itemKey are required")
	}
	if input.Metadata == nil {
		input.Metadata = map[string]any{}
	}
	return input, nil
}

func (s *Store) IsProcessed(input ProcessedItemInput) (ProcessedItemStatus, error) {
	input, err := normalizeProcessedInput(input)
	if err != nil {
		return ProcessedItemStatus{}, err
	}
	status := ProcessedItemStatus{}
	err = s.db.QueryRow(`
		SELECT processed_at FROM automation_processed_items
		WHERE profile_id = ? AND platform = ? AND item_type = ? AND item_key = ?`,
		input.ProfileID, input.Platform, input.ItemType, input.ItemKey,
	).Scan(&status.ProcessedAt)
	if err == sql.ErrNoRows {
		return status, nil
	}
	if err != nil {
		return ProcessedItemStatus{}, fmt.Errorf("query processed item: %w", err)
	}
	status.Processed = true
	return status, nil
}

func (s *Store) MarkProcessed(input ProcessedItemInput) (ProcessedItemStatus, error) {
	input, err := normalizeProcessedInput(input)
	if err != nil {
		return ProcessedItemStatus{}, err
	}
	metadataJSON, err := json.Marshal(input.Metadata)
	if err != nil {
		return ProcessedItemStatus{}, fmt.Errorf("marshal processed metadata: %w", err)
	}
	now := time.Now().Format(time.RFC3339)
	_, err = s.db.Exec(`
		INSERT INTO automation_processed_items
			(profile_id, platform, item_type, item_key, metadata_json, processed_at)
		VALUES (?, ?, ?, ?, ?, ?)
		ON CONFLICT(profile_id, platform, item_type, item_key) DO UPDATE SET
			metadata_json = excluded.metadata_json,
			processed_at = excluded.processed_at`,
		input.ProfileID, input.Platform, input.ItemType, input.ItemKey, string(metadataJSON), now,
	)
	if err != nil {
		return ProcessedItemStatus{}, fmt.Errorf("mark processed item: %w", err)
	}
	return ProcessedItemStatus{Processed: true, ProcessedAt: now}, nil
}

func normalizeCounterInput(input CounterIncrementInput) (CounterIncrementInput, error) {
	input.ProfileID = strings.TrimSpace(input.ProfileID)
	input.Platform = strings.ToLower(strings.TrimSpace(input.Platform))
	if input.Platform == "" {
		input.Platform = "generic"
	}
	input.CounterDate = strings.TrimSpace(input.CounterDate)
	if input.CounterDate == "" {
		input.CounterDate = time.Now().Format("2006-01-02")
	}
	input.CounterKey = strings.TrimSpace(input.CounterKey)
	if input.Delta == 0 {
		input.Delta = 1
	}
	if input.ProfileID == "" || input.CounterKey == "" {
		return input, fmt.Errorf("profileId and counterKey are required")
	}
	return input, nil
}

func (s *Store) IncrementCounter(input CounterIncrementInput) (DailyCounter, error) {
	input, err := normalizeCounterInput(input)
	if err != nil {
		return DailyCounter{}, err
	}
	now := time.Now().Format(time.RFC3339)
	_, err = s.db.Exec(`
		INSERT INTO automation_daily_counters
			(profile_id, platform, counter_date, counter_key, value, updated_at)
		VALUES (?, ?, ?, ?, ?, ?)
		ON CONFLICT(profile_id, platform, counter_date, counter_key) DO UPDATE SET
			value = automation_daily_counters.value + excluded.value,
			updated_at = excluded.updated_at`,
		input.ProfileID, input.Platform, input.CounterDate, input.CounterKey, input.Delta, now,
	)
	if err != nil {
		return DailyCounter{}, fmt.Errorf("increment automation counter: %w", err)
	}
	result := DailyCounter{
		ProfileID: input.ProfileID, Platform: input.Platform,
		CounterDate: input.CounterDate, CounterKey: input.CounterKey,
	}
	err = s.db.QueryRow(`
		SELECT value, updated_at FROM automation_daily_counters
		WHERE profile_id = ? AND platform = ? AND counter_date = ? AND counter_key = ?`,
		input.ProfileID, input.Platform, input.CounterDate, input.CounterKey,
	).Scan(&result.Value, &result.UpdatedAt)
	if err != nil {
		return DailyCounter{}, fmt.Errorf("query automation counter: %w", err)
	}
	return result, nil
}

func (s *Store) ListCounters(profileID, platform, counterDate string) ([]DailyCounter, error) {
	profileID = strings.TrimSpace(profileID)
	platform = strings.ToLower(strings.TrimSpace(platform))
	if platform == "" {
		platform = "generic"
	}
	counterDate = strings.TrimSpace(counterDate)
	if counterDate == "" {
		counterDate = time.Now().Format("2006-01-02")
	}
	if profileID == "" {
		return nil, fmt.Errorf("profileId is required")
	}
	rows, err := s.db.Query(`
		SELECT counter_key, value, updated_at FROM automation_daily_counters
		WHERE profile_id = ? AND platform = ? AND counter_date = ?
		ORDER BY counter_key`, profileID, platform, counterDate)
	if err != nil {
		return nil, fmt.Errorf("list automation counters: %w", err)
	}
	defer rows.Close()
	result := make([]DailyCounter, 0)
	for rows.Next() {
		item := DailyCounter{ProfileID: profileID, Platform: platform, CounterDate: counterDate}
		if err := rows.Scan(&item.CounterKey, &item.Value, &item.UpdatedAt); err != nil {
			return nil, fmt.Errorf("scan automation counter: %w", err)
		}
		result = append(result, item)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate automation counters: %w", err)
	}
	return result, nil
}
