package backend

import (
	"fmt"
	"strings"

	"ant-chrome/backend/internal/automationstate"
)

func (a *App) automationState() (*automationstate.Store, error) {
	if a.automationStateStore == nil {
		return nil, fmt.Errorf("automation account state store is unavailable")
	}
	return a.automationStateStore, nil
}

func (a *App) AutomationAccountStateGet(profileID, platform, scriptID string) (automationstate.AccountState, error) {
	store, err := a.automationState()
	if err != nil {
		return automationstate.AccountState{}, err
	}
	return store.GetState(profileID, platform, scriptID)
}

func (a *App) AutomationAccountKeywordsSave(profileID, platform, scriptID string, keywords []string) (automationstate.AccountState, error) {
	store, err := a.automationState()
	if err != nil {
		return automationstate.AccountState{}, err
	}
	return store.SaveKeywords(profileID, platform, scriptID, keywords)
}

func (a *App) AutomationAccountCursorSave(profileID, platform, scriptID string, cursor map[string]interface{}) (automationstate.AccountState, error) {
	store, err := a.automationState()
	if err != nil {
		return automationstate.AccountState{}, err
	}
	return store.SaveCursor(profileID, platform, scriptID, cursor)
}

func (a *App) AutomationProcessedItemCheck(input automationstate.ProcessedItemInput) (automationstate.ProcessedItemStatus, error) {
	store, err := a.automationState()
	if err != nil {
		return automationstate.ProcessedItemStatus{}, err
	}
	return store.IsProcessed(input)
}

func (a *App) AutomationProcessedItemMark(input automationstate.ProcessedItemInput) (automationstate.ProcessedItemStatus, error) {
	store, err := a.automationState()
	if err != nil {
		return automationstate.ProcessedItemStatus{}, err
	}
	return store.MarkProcessed(input)
}

func (a *App) AutomationDailyCounterIncrement(input automationstate.CounterIncrementInput) (automationstate.DailyCounter, error) {
	store, err := a.automationState()
	if err != nil {
		return automationstate.DailyCounter{}, err
	}
	return store.IncrementCounter(input)
}

func (a *App) AutomationDailyCounters(profileID, platform, counterDate string) ([]automationstate.DailyCounter, error) {
	store, err := a.automationState()
	if err != nil {
		return nil, err
	}
	return store.ListCounters(strings.TrimSpace(profileID), platform, counterDate)
}
