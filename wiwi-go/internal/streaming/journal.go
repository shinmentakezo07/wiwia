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
		if rec.ID <= afterID {
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
	return j.nextID[requestID]
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
