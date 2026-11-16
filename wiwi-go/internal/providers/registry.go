package providers

import (
	"fmt"
	"sort"
	"sync"
)

var (
	regMu    sync.RWMutex
	registry = map[string]func() Adapter{}
)

// Register associates a provider type with a factory that returns a fresh,
// private adapter instance (adapters hold per-stream decode state).
func Register(name string, factory func() Adapter) {
	regMu.Lock()
	defer regMu.Unlock()
	registry[name] = factory
}

// FreshAdapter returns a private adapter for the request hot path.
func FreshAdapter(name string) (Adapter, error) {
	regMu.RLock()
	f := registry[name]
	regMu.RUnlock()
	if f == nil {
		return nil, fmt.Errorf("unsupported provider type %q", name)
	}
	return f(), nil
}

// ListProviders returns all registered provider type names, sorted.
func ListProviders() []string {
	regMu.RLock()
	defer regMu.RUnlock()
	out := make([]string, 0, len(registry))
	for k := range registry {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// MustRegisterAll is called by the server at startup to assert that every type
// in config.PROVIDER_TYPES has an adapter. Returns the missing ones.
func MissingProviders(known []string) []string {
	var missing []string
	for _, k := range known {
		regMu.RLock()
		_, ok := registry[k]
		regMu.RUnlock()
		if !ok {
			missing = append(missing, k)
		}
	}
	return missing
}
