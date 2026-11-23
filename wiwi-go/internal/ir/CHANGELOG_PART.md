# IR Part API Additions

## Summary

Added missing constructor functions and accessor methods to bring the Go `ir.Part` API to parity with the Python IR implementation.

## Changes (2024-11-23)

### New Constructor Functions

#### `NewAudioPart(b64, mime string) Part`
Creates an audio content part with base64 data.
- Default mime: `audio/wav` when empty
- Used by: OpenAI Chat, Responses wire decoders

#### `NewDocumentPart(b64, url, mime string) Part`
Creates a document content part with either base64 data or URL.
- Default mime: `application/pdf` when empty
- Accepts both b64 and url (non-empty values are set)
- Used by: OpenAI Chat, Responses, Anthropic Messages wire decoders

#### `NewDocumentPartWithFileID(fileID string) Part`
Creates a document part referencing an Anthropic Files API file.
- Specialized constructor for file_id-based documents
- Used by: Anthropic Messages wire decoder

#### `SetDocumentMetadata(p Part, name, context string, cacheControl map[string]any) Part`
Adds optional metadata to a document part.
- Sets `name` (Anthropic block-level "title")
- Sets `context` (Anthropic block-level "context")
- Sets `cache_control` (prompt-cache breakpoint)
- Returns the modified part (payload mutation)

### New Accessor Methods

#### `Part.Int(key string) int`
Retrieves an integer value from the payload.
- Handles both `int` and `float64` (JSON numbers)
- Returns `0` for missing or non-numeric values
- Useful for: token counts, numeric limits

#### `Part.Slice(key string) []any`
Retrieves a slice from the payload.
- Returns `nil` for missing or non-slice values
- Useful for: `images`, `extra_blocks` fields in ToolResultPart

## Motivation

Before these additions, wire decoders (`chat.go`, `responses.go`, `messages.go`) constructed Audio and Document parts with inline `map[string]any` payloads (7+ locations). The new constructors:

1. **Ensure consistent field naming** across all part construction sites
2. **Reduce duplication** and maintenance burden
3. **Match the Python API surface** for easier cross-language work
4. **Provide ergonomic helpers** for Anthropic-specific features (file_id, metadata)

The accessor methods provide a uniform API for payload access, avoiding raw type assertions scattered throughout adapters and encoders.

## Testing

Added comprehensive test coverage in `part_test.go`:
- Constructor validation (default values, field presence)
- Accessor edge cases (missing keys, type mismatches)
- JSON round-trip verification
- Metadata chaining

All tests pass: `go test ./internal/ir/... -v`

## Backward Compatibility

✅ **Fully backward compatible**
- Existing code continues to work (payload map is unchanged)
- New constructors are additive only
- Accessors provide convenience, existing code can still use direct map access

## Related Files

- `wiwi-go/internal/ir/part.go` - Implementation
- `wiwi-go/internal/ir/part_test.go` - Test coverage
- `wiwi-go/internal/wire/chat.go` - Can now use `NewAudioPart`, `NewDocumentPart`
- `wiwi-go/internal/wire/responses.go` - Can now use `NewAudioPart`, `NewDocumentPart`
- `wiwi-go/internal/wire/messages.go` - Can now use `NewDocumentPartWithFileID`
- `wiwi-go/internal/core/tokenest.go` - Can now use `Part.Slice("images")`

## Python Parity

The Go IR now has feature parity with Python for:
- ✅ All part type constructors (Text, Image, ToolUse, ToolResult, Thinking, Audio, Document)
- ✅ Accessor methods for common payload types (Str, Bool, Map, Int, Slice)
- ✅ Document metadata handling (name, context, cache_control, file_id)

The only intentional difference: Python uses typed fields (`images: list[ImagePart]`) while Go uses a flexible payload map — but this is a design choice, not a gap.
