package streaming

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

// JournalRecord is one persisted SSE frame for a request's stream.
type JournalRecord struct {
	ID    int64  `json:"id"`
	Event string `json:"event,omitempty"`
	Data  string `json:"data"`
	Ts    int64  `json:"ts"`
	KeyID string `json:"key_id,omitempty"`
	// Done marks a terminal record: the stream finished and a reconnect must
	// replay the journal instead of re-running the upstream request (AUDIT
	// parity: replay must never bill a second generation).
	Done bool `json:"done,omitempty"`
}

// Journal persists encoded SSE frames per request so a reconnecting client can
// replay with Last-Event-ID, even across a gateway restart.
type Journal struct {
	dir        string
	ttl        time.Duration
	maxBytes   int64
	enabled    bool
	mu         sync.Mutex
	nextID     map[string]int64
	activeSize map[string]int64
}

func NewJournal(enabled bool, dir string, ttlS float64, maxBytes int64) *Journal {
	if dir == "" {
		dir = ".wiwi/journals"
	}
	if ttlS <= 0 {
		ttlS = 600
	}
	if maxBytes <= 0 {
		maxBytes = 1 << 20
	}
	j := &Journal{
		dir: dir, ttl: time.Duration(ttlS * float64(time.Second)), maxBytes: maxBytes,
		enabled: enabled, nextID: map[string]int64{}, activeSize: map[string]int64{},
	}
	if enabled {
		_ = os.MkdirAll(dir, 0o755)
	}
	return j
}

func (j *Journal) Enabled() bool { return j != nil && j.enabled }

func (j *Journal) path(requestID string) string {
	return filepath.Join(j.dir, requestID+".jsonl")
}

// Append writes one frame and returns its monotonic id. It fails open: a
// journal write must never break serving.
func (j *Journal) Append(requestID, keyID, event, data string) int64 {
	if !j.Enabled() || requestID == "" {
		return 0
	}
	j.mu.Lock()
	defer j.mu.Unlock()
	id := j.nextID[requestID] + 1
	if j.nextID[requestID] == 0 {
		// Resuming after a restart: continue the journal's numbering instead
		// of restarting at 1, which would replay the client's Last-Event-ID
		// against duplicate ids.
		if last := j.lastIDLocked(requestID); last >= id {
			id = last + 1
		}
	}
	j.nextID[requestID] = id
	rec := JournalRecord{ID: id, Event: event, Data: data, Ts: time.Now().Unix(), KeyID: keyID}
	b, _ := json.Marshal(rec)
	f, err := os.OpenFile(j.path(requestID), os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return id
	}
	defer f.Close()
	n, _ := f.Write(append(b, '\n'))
	j.activeSize[requestID] += int64(n)
	if j.activeSize[requestID] > j.maxBytes {
		// cap: drop the journal (a client cannot replay an over-cap stream)
		_ = os.Remove(j.path(requestID))
		j.activeSize[requestID] = 0
	}
	return id
}

// Replay returns records with ID > afterID for the request (and matching key).
func (j *Journal) Replay(requestID, keyID string, afterID int64) []JournalRecord {
	if !j.Enabled() || requestID == "" {
		return nil
	}
	j.mu.Lock()
	defer j.mu.Unlock()
	f, err := os.Open(j.path(requestID))
	if err != nil {
		return nil
	}
	defer f.Close()
	var out []JournalRecord
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 64*1024), 4*1024*1024)
	for sc.Scan() {
		var rec JournalRecord
		if json.Unmarshal(sc.Bytes(), &rec) != nil {
			continue
		}
		if rec.Done || rec.ID <= afterID {
			continue
		}
		if keyID != "" && rec.KeyID != "" && rec.KeyID != keyID {
			continue
		}
		out = append(out, rec)
	}
	return out
}

// Sweep removes journals older than the TTL.
func (j *Journal) Sweep() int {
	if !j.Enabled() {
		return 0
	}
	entries, err := os.ReadDir(j.dir)
	if err != nil {
		return 0
	}
	cutoff := time.Now().Add(-j.ttl)
	removed := 0
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".jsonl") {
			continue
		}
		info, err := e.Info()
		if err != nil {
			continue
		}
		if info.ModTime().Before(cutoff) {
			if os.Remove(filepath.Join(j.dir, e.Name())) == nil {
				removed++
			}
		}
	}
	return removed
}

// IDs returns the current last id for a request (for tests/diagnostics).
func (j *Journal) LastID(requestID string) int64 {
	j.mu.Lock()
	defer j.mu.Unlock()
	if last := j.nextID[requestID]; last > 0 {
		return last
	}
	return j.lastIDLocked(requestID)
}

// MarkDone records that this request's stream reached its terminal frame, so a
// reconnect replays the journal instead of re-running the upstream request
// (which would duplicate content and bill a second generation).
func (j *Journal) MarkDone(requestID, keyID string) {
	if !j.Enabled() || requestID == "" {
		return
	}
	j.mu.Lock()
	defer j.mu.Unlock()
	rec := JournalRecord{ID: j.nextID[requestID] + 1, Done: true, Ts: time.Now().Unix(), KeyID: keyID}
	j.nextID[requestID] = rec.ID
	b, _ := json.Marshal(rec)
	f, err := os.OpenFile(j.path(requestID), os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return
	}
	defer f.Close()
	n, _ := f.Write(append(b, '\n'))
	j.activeSize[requestID] += int64(n)
}

// IsDone reports whether the journal already holds this request's terminal
// frame.
func (j *Journal) IsDone(requestID string) bool {
	if !j.Enabled() || requestID == "" {
		return false
	}
	j.mu.Lock()
	defer j.mu.Unlock()
	f, err := os.Open(j.path(requestID))
	if err != nil {
		return false
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 64*1024), 4*1024*1024)
	done := false
	for sc.Scan() {
		var rec JournalRecord
		if json.Unmarshal(sc.Bytes(), &rec) != nil {
			continue
		}
		if rec.Done {
			done = true
		}
	}
	return done
}

// lastIDLocked reads the highest persisted id straight from the journal file,
// used when this process did not write the stream (restart resume).
func (j *Journal) lastIDLocked(requestID string) int64 {
	f, err := os.Open(j.path(requestID))
	if err != nil {
		return 0
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 64*1024), 4*1024*1024)
	var last int64
	for sc.Scan() {
		var rec JournalRecord
		if json.Unmarshal(sc.Bytes(), &rec) != nil {
			continue
		}
		if rec.ID > last {
			last = rec.ID
		}
	}
	return last
}

// List returns journal request ids sorted by modification time (newest first).
func (j *Journal) List() []string {
	entries, err := os.ReadDir(j.dir)
	if err != nil {
		return nil
	}
	type item struct {
		name string
		mod  time.Time
	}
	var items []item
	for _, e := range entries {
		if strings.HasSuffix(e.Name(), ".jsonl") {
			if info, err := e.Info(); err == nil {
				items = append(items, item{strings.TrimSuffix(e.Name(), ".jsonl"), info.ModTime()})
			}
		}
	}
	sort.Slice(items, func(a, b int) bool { return items[a].mod.After(items[b].mod) })
	out := make([]string, len(items))
	for i, it := range items {
		out[i] = it.name
	}
	return out
}

var _ = fmt.Sprintf
