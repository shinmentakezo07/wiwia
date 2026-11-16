package main

import (
	"fmt"
	"log"
	"os"
	"strconv"

	"wiwi-go/internal/config"
	"wiwi-go/internal/server"
)

func main() {
	config.LoadEnv()
	var cfgPath string
	portOverride := 0
	for i := 1; i < len(os.Args); i++ {
		switch os.Args[i] {
		case "--config", "-c":
			if i+1 < len(os.Args) {
				cfgPath = os.Args[i+1]
				i++
			}
		case "--port", "-p":
			if i+1 < len(os.Args) {
				if n, err := strconv.Atoi(os.Args[i+1]); err == nil {
					portOverride = n
				}
				i++
			}
		}
	}
	cfg, err := config.Resolve(cfgPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "wiwi: config error: %v\n", err)
		os.Exit(1)
	}
	app, err := server.New(cfg)
	if err != nil {
		fmt.Fprintf(os.Stderr, "wiwi: startup error: %v\n", err)
		os.Exit(1)
	}
	host := cfg.Wiwi.Host
	port := cfg.Wiwi.Port
	if host == "" {
		host = "0.0.0.0"
	}
	if portOverride > 0 {
		port = portOverride
	}
	if port == 0 {
		port = 4000
	}
	addr := fmt.Sprintf("%s:%d", host, port)
	log.Printf("wiwi listening on http://%s (%d deployments, %d providers)",
		addr, len(cfg.ModelList), len(cfg.Providers))
	if err := app.Router.Run(addr); err != nil {
		log.Fatalf("wiwi: %v", err)
	}
}
