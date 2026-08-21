package automation

import "testing"

func TestActiveTasksAndStopTasksByProfileAreIsolated(t *testing.T) {
	m := &Manager{
		activeTasks:     make(map[string]*activeTask),
		profileTaskPool: make(map[string]map[string]bool),
	}

	firstA, err := m.registerTask("profile-a")
	if err != nil {
		t.Fatal(err)
	}
	secondA, err := m.registerTask("profile-a")
	if err != nil {
		t.Fatal(err)
	}
	firstB, err := m.registerTask("profile-b")
	if err != nil {
		t.Fatal(err)
	}

	if got := len(m.ActiveTasks()); got != 3 {
		t.Fatalf("ActiveTasks() length = %d, want 3", got)
	}
	if got := m.StopTasksByProfile("profile-a"); got != 2 {
		t.Fatalf("StopTasksByProfile(profile-a) = %d, want 2", got)
	}

	remaining := m.ActiveTasks()
	if len(remaining) != 1 || remaining[0].TaskID != firstB || remaining[0].ProfileID != "profile-b" {
		t.Fatalf("remaining tasks = %#v, want only profile-b", remaining)
	}
	if _, ok := m.activeTasks[firstA]; ok {
		t.Fatalf("task %s should have been removed", firstA)
	}
	if _, ok := m.activeTasks[secondA]; ok {
		t.Fatalf("task %s should have been removed", secondA)
	}
}
