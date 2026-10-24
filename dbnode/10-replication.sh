#!/bin/bash
# Kører kun ved første initialisering af en primary: tillad replikaer at streame med samme bruger/adgangskode.
set -e
echo "host replication all all scram-sha-256" >> "$PGDATA/pg_hba.conf"
