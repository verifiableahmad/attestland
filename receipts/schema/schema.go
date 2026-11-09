// Package schema embeds the versioned receipt JSON schema.
package schema

import (
	"bytes"
	_ "embed"
	"encoding/json"
	"fmt"

	"github.com/santhosh-tekuri/jsonschema/v6"
)

//go:embed receipt-v1.schema.json
var V1 []byte

var compiled *jsonschema.Schema

func init() {
	doc, err := jsonschema.UnmarshalJSON(bytes.NewReader(V1))
	if err != nil {
		panic(err)
	}
	c := jsonschema.NewCompiler()
	c.AssertFormat()
	if err := c.AddResource("receipt-v1.schema.json", doc); err != nil {
		panic(err)
	}
	compiled = c.MustCompile("receipt-v1.schema.json")
}

// Validate checks raw receipt JSON against the v1 schema.
func Validate(raw []byte) error {
	var v any
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	if err := dec.Decode(&v); err != nil {
		return fmt.Errorf("not JSON: %w", err)
	}
	return compiled.Validate(v)
}
