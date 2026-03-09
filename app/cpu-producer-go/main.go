package main

import (
	"fmt"
	"log"
	"os"
	"strconv"
	"time"

	amqp "github.com/rabbitmq/amqp091-go"
)

func getenv(key, def string) string {
	v := os.Getenv(key)
	if v == "" {
		return def
	}
	return v
}

func getenvInt(key string, def int) int {
	v := os.Getenv(key)
	if v == "" {
		return def
	}
	n, err := strconv.Atoi(v)
	if err != nil {
		return def
	}
	return n
}

func connect(url string) *amqp.Connection {
	for {
		conn, err := amqp.Dial(url)
		if err == nil {
			return conn
		}
		log.Printf("falha ao conectar no RabbitMQ: %v", err)
		time.Sleep(5 * time.Second)
	}
}

func main() {
	rabbitURL := getenv("RABBITMQ_URL", "amqp://admin:admin123@rabbitmq.messaging.svc.cluster.local:5672/")
	queueName := getenv("QUEUE_NAME", "cpu-jobs")
	messageCount := getenvInt("MESSAGE_COUNT", 50)
	messagePrefix := getenv("MESSAGE_PREFIX", "job")
	messageDelayMs := getenvInt("MESSAGE_DELAY_MS", 20)

	log.Printf("iniciando producer; queue=%s messageCount=%d", queueName, messageCount)

	conn := connect(rabbitURL)
	defer conn.Close()

	ch, err := conn.Channel()
	if err != nil {
		log.Fatalf("falha ao abrir channel: %v", err)
	}
	defer ch.Close()

	_, err = ch.QueueDeclare(
		queueName,
		true,
		false,
		false,
		false,
		nil,
	)
	if err != nil {
		log.Fatalf("falha ao declarar fila: %v", err)
	}

	for i := 1; i <= messageCount; i++ {
		body := fmt.Sprintf("%s-%d-%d", messagePrefix, time.Now().Unix(), i)

		err = ch.Publish(
			"",
			queueName,
			false,
			false,
			amqp.Publishing{
				ContentType:  "text/plain",
				Body:         []byte(body),
				DeliveryMode: amqp.Persistent,
				Timestamp:    time.Now(),
			},
		)
		if err != nil {
			log.Fatalf("falha ao publicar mensagem %d: %v", i, err)
		}

		log.Printf("mensagem publicada: %s", body)

		if messageDelayMs > 0 {
			time.Sleep(time.Duration(messageDelayMs) * time.Millisecond)
		}
	}

	log.Printf("burst finalizado; entrando em idle")

	for {
		time.Sleep(1 * time.Hour)
	}
}
