CINDER_BRANCH ?= master # master, stable/ocata, refs/changes/67/418167/1
NAME_PREFIX ?= ""
PLATFORM ?= ubuntu # debian, centos
TAG ?= latest

all: base lvm devbox keystone

base:
	docker build https://git.openstack.org/openstack/loci-cinder.git\#:$(PLATFORM) --tag cinder:$(TAG) --build-arg PROJECT_REF="stable/ocata"

lvm:
	docker build -t cinder-lvm -f ./docker_files/Dockerfile.cinder-lvm .

devbox:
	cp ../../test-requirements.txt ./docker_files/
	docker build -t cinder-devenv -f ./docker_files/Dockerfile.cinder-devenv .
	rm ./docker_files/test-requirements.txt

keystone:
    docker build https://git.openstack.org/openstack/loci-keystone.git\#:$(PLATFORM) --tag keystone:$(PLATFORM) --build-arg PROJECT_REF="stable/ocata"