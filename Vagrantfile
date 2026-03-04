# -*- mode: ruby -*-
# vi: set ft=ruby :

$prep_script = <<-SCRIPT
  echo "Preparando o SO para Kubernetes..."
  swapoff -a
  sed -i '/ swap / s/^\(.*\)$/#\1/g' /etc/fstab
  setenforce 0
  sed -i 's/^SELINUX=enforcing$/SELINUX=permissive/' /etc/selinux/config
  systemctl disable --now firewalld
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
  end
end