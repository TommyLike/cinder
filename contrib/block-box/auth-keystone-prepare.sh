openstack project create --domain default --description "Service Project" service
openstack user create --domain default --password password cinder
openstack role add --project service --user cinder admin

openstack service create --name cinder --description "OpenStack Block Storage" volume
openstack service create --name cinderv2 --description "OpenStack Block Storage" volumev2
openstack service create --name cinderv3 --description "OpenStack Block Storage" volumev3
openstack endpoint create --region RegionOne volume public http://172.49.49.8:8776/v1/%\(project_id\)s
openstack endpoint create --region RegionOne volumev2 public http://172.49.49.8:8776/v2/%\(project_id\)s
openstack endpoint create --region RegionOne volumev3 public http://172.49.49.8:8776/v3/%\(project_id\)s
