package receipt

import (
	"bufio"
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"

	"github.com/gowebpki/jcs"
)

// FilesRoot hashes a model directory exactly as workloads/train/export_hf.py
// does: SHA-256 per regular file (files.sha256 excluded), then SHA-256 over the
// sorted "name:hexdigest\n" lines. Only the top level is hashed.
func FilesRoot(dir string) (string, int, error) {
	ents, err := os.ReadDir(dir)
	if err != nil {
		return "", 0, err
	}
	var lines []string
	for _, e := range ents {
		if !e.Type().IsRegular() || e.Name() == "files.sha256" {
			continue
		}
		f, err := os.Open(filepath.Join(dir, e.Name()))
		if err != nil {
			return "", 0, err
		}
		h := sha256.New()
		_, err = io.Copy(h, f)
		f.Close()
		if err != nil {
			return "", 0, err
		}
		lines = append(lines, e.Name()+":"+hex.EncodeToString(h.Sum(nil))+"\n")
	}
	sort.Strings(lines)
	root := sha256.New()
	for _, l := range lines {
		root.Write([]byte(l))
	}
	return hex.EncodeToString(root.Sum(nil)), len(lines), nil
}

// CanonicalJSONL rewrites JSONL so each line is the RFC 8785 form of its
// object, newline-terminated; blank lines are dropped. Same records in the
// same order always give the same bytes.
func CanonicalJSONL(in io.Reader, out io.Writer) (int, error) {
	sc := bufio.NewScanner(in)
	sc.Buffer(make([]byte, 1<<20), 64<<20)
	n := 0
	for sc.Scan() {
		line := bytes.TrimSpace(sc.Bytes())
		if len(line) == 0 {
			continue
		}
		if !json.Valid(line) {
			return n, fmt.Errorf("line %d: not JSON", n+1)
		}
		c, err := jcs.Transform(line)
		if err != nil {
			return n, fmt.Errorf("line %d: %w", n+1, err)
		}
		if _, err := out.Write(append(c, '\n')); err != nil {
			return n, err
		}
		n++
	}
	return n, sc.Err()
}

// JSONLRoot returns (count, SHA-256 of the canonical JSONL bytes). v1 hashes
// the whole canonical file; v2 will build a Merkle tree over request-response pairs.
func JSONLRoot(path string) (int, string, error) {
	f, err := os.Open(path)
	if err != nil {
		return 0, "", err
	}
	defer f.Close()
	h := sha256.New()
	n, err := CanonicalJSONL(f, h)
	if err != nil {
		return 0, "", err
	}
	return n, hex.EncodeToString(h.Sum(nil)), nil
}
