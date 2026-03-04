# -*- mode: ruby -*-
# vi: set ft=ruby :

$prep_script = <<-SCRIPT
  echo "Preparando o SO para Kubernetes..."
  swapoff -a
  sed -i '/ swap / s/^\(.*\)$/#\1/g' /etc/fstab
  setenforce 0
  sed -i 's/^SELINUX=enforcing$/SELINUX=permissive/' /etc/selinux/config
  # O '|| true' garante que o script não pare se o serviço não existir
  systemctl disable --now firewalld || true
SCRIPT

$k8s_install_script = <<-SCRIPT
  echo "Instalando Containerd e ferramentas K8s..."
  
  # Configurar módulos do kernel para o Containerd
  cat <<EOF | tee /etc/modules-load.d/containerd.conf
overlay
br_netfilter
EOF
  modprobe overlay
  modprobe br_netfilter

  # Parâmetros de rede para o Kubernetes
  cat <<EOF | tee /etc/sysctl.d/99-kubernetes-cri.conf
net.bridge.bridge-nf-call-iptables  = 1
net.ipv4.ip_forward                 = 1
net.bridge.bridge-nf-call-ip6tables = 1
EOF
  sysctl --system

  # Instalar Containerd (AlmaLinux/CentOS repos)
  dnf config-manager --add-repo=https://download.docker.com/linux/centos/docker-ce.repo
  dnf install -y containerd.io
  mkdir -p /etc/containerd
  containerd config default | tee /etc/containerd/config.toml
  sed -i 's/SystemdCgroup = false/SystemdCgroup = true/' /etc/containerd/config.toml
  systemctl enable --now containerd

  # Configurar Repositório Kubernetes v1.30
  cat <<EOF | tee /etc/yum.repos.d/kubernetes.repo
[kubernetes]
name=Kubernetes
baseurl=https://pkgs.k8s.io/core:/stable:/v1.30/rpm/
enabled=1
gpgcheck=1
gpgkey=https://pkgs.k8s.io/core:/stable:/v1.30/rpm/repodata/repomd.xml.key
EOF

  dnf install -y kubelet kubeadm kubectl --disableexcludes=kubernetes
  systemctl enable --now kubelet
SCRIPT

Vagrant.configure("2") do |config|
  # Usando AlmaLinux 9 para replicar um ambiente enterprise Red Hat
  config.vm.box = "almalinux/9"
  
  config.vm.provider "vmware_desktop" do |v|
    v.gui = true
  end

  # Control Plane
  config.vm.define "k8s-master" do |master|
    master.vm.hostname = "k8s-master"
    master.vm.network "private_network", ip: "192.168.100.10"
    master.vm.provider "vmware_desktop" do |v|
      v.vmx["numvcpus"] = "2"
      v.vmx["memsize"] = "4096"
    end
    master.vm.provision "shell", inline: $prep_script
    master.vm.provision "shell", inline: $k8s_install_script
  end

  # Worker 01
  config.vm.define "k8s-worker-01" do |worker1|
    worker1.vm.hostname = "k8s-worker-01"
    worker1.vm.network "private_network", ip: "192.168.100.21"
    worker1.vm.provider "vmware_desktop" do |v|
      v.vmx["numvcpus"] = "2"
      v.vmx["memsize"] = "2048"
    end
    worker1.vm.provision "shell", inline: $prep_script
    worker1.vm.provision "shell", inline: $k8s_install_script
  end

  # Worker 02
  config.vm.define "k8s-worker-02" do |worker2|
    worker2.vm.hostname = "k8s-worker-02"
    worker2.vm.network "private_network", ip: "192.168.100.22"
    worker2.vm.provider "vmware_desktop" do |v|
      v.vmx["numvcpus"] = "2"
      v.vmx["memsize"] = "2048"
    end
    worker2.vm.provision "shell", inline: $prep_script
    worker2.vm.provision "shell", inline: $k8s_install_script
  end
end