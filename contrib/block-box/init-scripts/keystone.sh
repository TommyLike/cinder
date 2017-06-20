function install_apache_uwsgi {
    local apxs="apxs2"
    local dir
    dir=$(mktemp -d)
    pushd $dir
    pip install uwsgi
    pip download uwsgi
    local uwsgi
    uwsgi=$(ls uwsgi*)
    tar xvf $uwsgi
    cd uwsgi*/apache2
    sudo $apxs -i -c mod_proxy_uwsgi.c
    popd
    # delete the temp directory
    sudo rm -rf $dir

    # we've got to enable proxy and proxy_uwsgi for this to work
    sudo a2enmod proxy
    sudo a2enmod proxy_uwsgi
    sudo service apache2 restart
}
apt-get update
apt-get install  -y --no-install-recommends  memcached python-memcache  apache2-dev gcc libapache2-mod-proxy-uwsgi
/bin/sh -c "keystone-manage db_sync"
/bin/sh -c "keystone-manage fernet_setup --keystone-user keystone --keystone-group keystone"
/bin/sh -c "keystone-manage bootstrap --bootstrap-username admin --bootstrap-password password --bootstrap-project-name admin --bootstrap-role-name admin --bootstrap-service-name keystone --bootstrap-region-id RegionOne --bootstrap-admin-url http://172.49.49.7/identity --bootstrap-public-url http://172.49.49.7/identity"
install_apache_uwsgi
cp /etc/keystone/keystone-wsgi-public.ini /etc/apache2/sites-available/
mv /etc/apache2/sites-available/keystone-wsgi-public.ini /etc/apache2/sites-available/keystone-wsgi-public.conf
a2ensite keystone-wsgi-public
service apache2 restart
mkdir -p /var/run/uwsgi
pip install -v SQLAlchemy==1.0.10
/var/lib/openstack/bin/uwsgi --ini /etc/keystone/keystone-uwsgi-public.ini
