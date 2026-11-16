package cost

// defaultPrices is the bundled fallback table, used until an operator or the
// persistence layer supplies its own. Values are USD per token; the comment on
// each line is the equivalent USD per million tokens.
var defaultPrices = map[string]ModelPrice{
	"gpt-4o": { // $2.50 / $10.00 / $1.25
		Prompt: 2.50 / 1e6, Completion: 10.00 / 1e6, Cached: 1.25 / 1e6,
	},
	"gpt-4o-mini": { // $0.15 / $0.60 / $0.075
		Prompt: 0.15 / 1e6, Completion: 0.60 / 1e6, Cached: 0.075 / 1e6,
	},
	"gpt-4.1": { // $2.00 / $8.00 / $0.50
		Prompt: 2.00 / 1e6, Completion: 8.00 / 1e6, Cached: 0.50 / 1e6,
	},
	"gpt-4.1-mini": { // $0.40 / $1.60 / $0.10
		Prompt: 0.40 / 1e6, Completion: 1.60 / 1e6, Cached: 0.10 / 1e6,
	},
	"o1": { // $15.00 / $60.00 / $7.50
		Prompt: 15.00 / 1e6, Completion: 60.00 / 1e6, Cached: 7.50 / 1e6,
	},
	"o3": { // $2.00 / $8.00 / $0.50
		Prompt: 2.00 / 1e6, Completion: 8.00 / 1e6, Cached: 0.50 / 1e6,
	},
	"o3-mini": { // $1.10 / $4.40 / $0.55
		Prompt: 1.10 / 1e6, Completion: 4.40 / 1e6, Cached: 0.55 / 1e6,
	},
	"claude-3-5-sonnet": { // $3.00 / $15.00 / $0.30
		Prompt: 3.00 / 1e6, Completion: 15.00 / 1e6, Cached: 0.30 / 1e6,
	},
	"claude-sonnet-4": { // $3.00 / $15.00 / $0.30
		Prompt: 3.00 / 1e6, Completion: 15.00 / 1e6, Cached: 0.30 / 1e6,
	},
	"claude-opus-4": { // $15.00 / $75.00 / $1.50
		Prompt: 15.00 / 1e6, Completion: 75.00 / 1e6, Cached: 1.50 / 1e6,
	},
	"claude-3-5-haiku": { // $0.80 / $4.00 / $0.08
		Prompt: 0.80 / 1e6, Completion: 4.00 / 1e6, Cached: 0.08 / 1e6,
	},
	"gemini-1.5-pro": { // $1.25 / $5.00 / $0.3125
		Prompt: 1.25 / 1e6, Completion: 5.00 / 1e6, Cached: 0.3125 / 1e6,
	},
	"gemini-2.0-flash": { // $0.10 / $0.40 / $0.025
		Prompt: 0.10 / 1e6, Completion: 0.40 / 1e6, Cached: 0.025 / 1e6,
	},
	"deepseek-chat": { // $0.27 / $1.10 / $0.07
		Prompt: 0.27 / 1e6, Completion: 1.10 / 1e6, Cached: 0.07 / 1e6,
	},
	"llama-3.1-70b": { // $0.52 / $0.75, no cache discount
		Prompt: 0.52 / 1e6, Completion: 0.75 / 1e6,
	},
}
