# Postgres/PostGIS-node til DOF.tool (primary eller replika). Se dbadmin → Token / script.
FROM postgis/postgis:17-3.5
COPY 10-replication.sh /docker-entrypoint-initdb.d/10-replication.sh
COPY node-entrypoint.sh /usr/local/bin/node-entrypoint.sh
RUN chmod +x /docker-entrypoint-initdb.d/10-replication.sh /usr/local/bin/node-entrypoint.sh
ENTRYPOINT ["node-entrypoint.sh"]
CMD ["postgres"]
