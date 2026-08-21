package launchcode

import (
	"encoding/json"
	"io"
	"net/http"
	"strings"

	"ant-chrome/backend/internal/automationstate"
)

type automationAccountStateWriteRequest struct {
	ProfileID string                  `json:"profileId"`
	Platform  string                  `json:"platform"`
	ScriptID  string                  `json:"scriptId"`
	Keywords  *[]string               `json:"keywords"`
	Cursor    *map[string]interface{} `json:"cursor"`
}

func (s *LaunchServer) handleAutomationAccountState(w http.ResponseWriter, r *http.Request) {
	provider := s.automationStateProvider()
	if provider == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]interface{}{"ok": false, "error": "automation account state is unavailable"})
		return
	}

	switch r.Method {
	case http.MethodGet:
		state, err := provider.GetState(r.URL.Query().Get("profileId"), r.URL.Query().Get("platform"), r.URL.Query().Get("scriptId"))
		if err != nil {
			writeAutomationStateError(w, err)
			return
		}
		writeJSON(w, http.StatusOK, map[string]interface{}{"ok": true, "state": state})
	case http.MethodPut:
		var req automationAccountStateWriteRequest
		if err := decodeAutomationStateBody(r, &req); err != nil {
			writeAutomationStateError(w, err)
			return
		}
		state, err := provider.GetState(req.ProfileID, req.Platform, req.ScriptID)
		if err == nil && req.Keywords != nil {
			state, err = provider.SaveKeywords(req.ProfileID, req.Platform, req.ScriptID, *req.Keywords)
		}
		if err == nil && req.Cursor != nil {
			state, err = provider.SaveCursor(req.ProfileID, req.Platform, req.ScriptID, *req.Cursor)
		}
		if err != nil {
			writeAutomationStateError(w, err)
			return
		}
		writeJSON(w, http.StatusOK, map[string]interface{}{"ok": true, "state": state})
	default:
		w.Header().Set("Allow", "GET, PUT")
		writeJSON(w, http.StatusMethodNotAllowed, map[string]interface{}{"ok": false, "error": "method not allowed"})
	}
}

func (s *LaunchServer) handleAutomationProcessedCheck(w http.ResponseWriter, r *http.Request) {
	provider := s.requireAutomationStateProvider(w, r, http.MethodPost)
	if provider == nil {
		return
	}
	var input automationstate.ProcessedItemInput
	if err := decodeAutomationStateBody(r, &input); err != nil {
		writeAutomationStateError(w, err)
		return
	}
	status, err := provider.IsProcessed(input)
	if err != nil {
		writeAutomationStateError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{"ok": true, "status": status})
}

func (s *LaunchServer) handleAutomationProcessedMark(w http.ResponseWriter, r *http.Request) {
	provider := s.requireAutomationStateProvider(w, r, http.MethodPost)
	if provider == nil {
		return
	}
	var input automationstate.ProcessedItemInput
	if err := decodeAutomationStateBody(r, &input); err != nil {
		writeAutomationStateError(w, err)
		return
	}
	status, err := provider.MarkProcessed(input)
	if err != nil {
		writeAutomationStateError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{"ok": true, "status": status})
}

func (s *LaunchServer) handleAutomationCounterIncrement(w http.ResponseWriter, r *http.Request) {
	provider := s.requireAutomationStateProvider(w, r, http.MethodPost)
	if provider == nil {
		return
	}
	var input automationstate.CounterIncrementInput
	if err := decodeAutomationStateBody(r, &input); err != nil {
		writeAutomationStateError(w, err)
		return
	}
	counter, err := provider.IncrementCounter(input)
	if err != nil {
		writeAutomationStateError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{"ok": true, "counter": counter})
}

func (s *LaunchServer) handleAutomationCounters(w http.ResponseWriter, r *http.Request) {
	provider := s.requireAutomationStateProvider(w, r, http.MethodGet)
	if provider == nil {
		return
	}
	counters, err := provider.ListCounters(r.URL.Query().Get("profileId"), r.URL.Query().Get("platform"), r.URL.Query().Get("date"))
	if err != nil {
		writeAutomationStateError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{"ok": true, "counters": counters})
}

func (s *LaunchServer) requireAutomationStateProvider(w http.ResponseWriter, r *http.Request, method string) AutomationStateProvider {
	if r.Method != method {
		w.Header().Set("Allow", method)
		writeJSON(w, http.StatusMethodNotAllowed, map[string]interface{}{"ok": false, "error": "method not allowed"})
		return nil
	}
	provider := s.automationStateProvider()
	if provider == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]interface{}{"ok": false, "error": "automation account state is unavailable"})
		return nil
	}
	return provider
}

func decodeAutomationStateBody(r *http.Request, target interface{}) error {
	dec := json.NewDecoder(io.LimitReader(r.Body, 1<<20))
	if err := dec.Decode(target); err != nil {
		return &automationStateRequestError{message: "invalid request body: " + err.Error()}
	}
	return nil
}

type automationStateRequestError struct{ message string }

func (e *automationStateRequestError) Error() string { return e.message }

func writeAutomationStateError(w http.ResponseWriter, err error) {
	status := http.StatusInternalServerError
	if _, ok := err.(*automationStateRequestError); ok || strings.Contains(strings.ToLower(err.Error()), "required") {
		status = http.StatusBadRequest
	}
	writeJSON(w, status, map[string]interface{}{"ok": false, "error": err.Error()})
}
