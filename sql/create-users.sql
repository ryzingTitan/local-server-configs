CREATE USER cashcub WITH PASSWORD 'password';
GRANT CREATE ON DATABASE cash_cub TO cashcub;

CREATE USER obdtrak WITH PASSWORD 'password';
GRANT CREATE ON SCHEMA obd_trak TO obdtrak;
GRANT USAGE ON SCHEMA obd_trak TO obdtrak;