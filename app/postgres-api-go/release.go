package main

import "log"

const releaseVersion = "canary-lab-v2"

func init() {
	log.Printf("release=%s progressive_delivery=argo-rollouts", releaseVersion)
}
