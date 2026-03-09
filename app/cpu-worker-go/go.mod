package main

import (
	"crypto/sha256"
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

func burnCPU(seconds int) {
	deadline := time.Now().Add(time.Duration(seconds) * time.Second)
	data := []byte("cpu-worker-go")

	for time.Now().Before(deadline) {
		sum := sha256.Sum256(data)
		data = sum[:]
	}
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
	workSeconds := getenvInt("WORK_SECONDS", 8)

	log.Printf("iniciando worker; queue=%s workSeconds=%d", queueName, workSeconds)

	for {
		conn := connect(rabbitURL)

		ch, err := conn.Channel()
		if err != nil {
			log.Printf("falha ao abrir channel: %v", err)
			_ = conn.Close()
			time.Sleep(3 * time.Second)
			continue
		}

		_, err = ch.QueueDeclare(
			queueName,
			true,
			false,
			false,
			false,
			nil,
		)
		if err != nil {
			log.Printf("falha ao declarar fila: %v", err)
			_ = ch.Close()
			_ = conn.Close()
			time.Sleep(3 * time.Second)
			continue
		}

		if err := ch.Qos(1, 0, false); err != nil {
			log.Printf("falha ao configurar qos: %v", err)
		}

		msgs, err := ch.Consume(
			queueName,
			"",
			false,
			false,
			false,
			false,
			nil,
		)
		if err != nil {
			log.Printf("falha ao consumir fila: %v", err)
			_ = ch.Close()
			_ = conn.Close()
			time.Sleep(3 * time.Second)
			continue
		}

		log.Printf("conectado no RabbitMQ; aguardando mensagens")

		closed := make(chan *amqp.Error, 1)
		ch.NotifyClose(closed)

	consumeLoop:
		for {
			select {
			case msg, ok := <-msgs:
				if !ok {
					log.Printf("canal de consumo encerrado")
					break consumeLoop
				}

				log.Printf("mensagem recebida: %s", string(msg.Body))
				start := time.Now()

				burnCPU(workSeconds)

				if err := msg.Ack(false); err != nil {
					log.Printf("falha ao dar ack: %v", err)
				}

				log.Printf("mensagem processada em %s", time.Since(start).String())

			case err := <-closed:
				if err != nil {
					log.Printf("channel fechado: %v", err)
				} else {
					log.Printf("channel fechado")
				}
				break consumeLoop
			}
		}

		_ = ch.Close()
		_ = conn.Close()
		time.Sleep(3 * time.Second)
	}
}
