package streaming

import (
	"testing"
)

func newTestJournal(t *testing.T) *Journal {
	t.Helper()
	return NewJournal(true, t.TempDir(), 60, 1<<20)
}

// TestReplayAfterDoneIsTerminal: once the stream finished, a reconnect must be
// served entirely from the journal. Re-running the upstream request would
// duplicate the answer and bill a second generation.
func TestReplayAfterDoneIsTerminal(t *testing.T) {
	j := newTestJournal(t)
	id1 := j.Append("req1", "key1", "", `{"n":1}`)
	id2 := j.Append("req1", "key1", "", `{"n":2}`)
	if got := j.Replay("req1", "key1", 0); len(got) != 2 {
		t.Fatalf("expected 2 records, got %d", len(got))
	}
	if j.IsDone("req1") {
		t.Fatal("a live stream must not report done")
	}
	j.MarkDone("req1", "key1")
	if !j.IsDone("req1") {
		t.Fatal("MarkDone must mark the journal done")
	}
	// A resume from the first frame replays only what is missing, and the
	// terminal marker is never replayed as a data frame.
	got := j.Replay("req1", "key1", id1)
	if len(got) != 1 || got[0].ID != id2 {
		t.Fatalf("expected only the second frame, got %+v", got)
	}
	if len(j.Replay("req1", "key1", id2)) != 0 {
		t.Fatal("nothing is new after the last frame")
	}
}

// TestJournalIDsContinueAcrossRestart: a fresh Journal over an existing file
// continues the numbering instead of restarting at 1, which would make a
// client's Last-Event-ID replay duplicate ids.
func TestJournalIDsContinueAcrossRestart(t *testing.T) {
	dir := t.TempDir()
	j1 := NewJournal(true, dir, 60, 1<<20)
	j1.Append("req", "key", "", `{"a":1}`)
	j1.Append("req", "key", "", `{"a":2}`)
	j1.MarkDone("req", "key")

	j2 := NewJournal(true, dir, 60, 1<<20)
	if last := j2.LastID("req"); last != 3 {
		t.Fatalf("expected the journal to resume at 3, got %d", last)
	}
	if id := j2.Append("req", "key", "", `{"a":3}`); id != 4 {
		t.Fatalf("expected id 4 after restart, got %d", id)
	}
}

// TestJournalDisabledWritesNothing: stream_journal_enabled=false must not leave
// files behind and must not hand out ids.
func TestJournalDisabledWritesNothing(t *testing.T) {
	j := NewJournal(false, t.TempDir(), 60, 1<<20)
	if id := j.Append("req", "key", "", `{"a":1}`); id != 0 {
		t.Fatalf("disabled journal returned id %d", id)
	}
	if j.IsDone("req") {
		t.Fatal("disabled journal must not report done")
	}
	if len(j.Replay("req", "key", 0)) != 0 {
		t.Fatal("disabled journal must not replay")
	}
}
