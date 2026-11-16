package providers

// Thin OpenAI-wire variants. Each of these provider types speaks the OpenAI
// Chat Completions shape once a request is admitted, so they reuse
// OpenAIAdapter with their own providerType (which gates the fields a strict
// gateway rejects, e.g. reasoning_effort).
//
// OAuth wiring is deliberately out of scope here: credential acquisition and
// refresh for cline / workbuddy / hermes (device-code flows, version
// handshakes, auto-refresh) live in the provider-specific auth packages of the
// Python implementation, not in the outbound wire adapter. The adapters below
// receive an already-resolved secret through ProviderKeyRef.
func init() {
	for _, name := range []string{"cline", "workbuddy", "hermes", "ifm"} {
		providerType := name
		Register(name, func() Adapter { return &OpenAIAdapter{providerType: providerType} })
	}
}
