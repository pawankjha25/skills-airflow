import testing.postgresql
from airflow import configuration
from datetime import datetime
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine
from airflow import models
from airflow.jobs import BackfillJob
from mock import patch
from dags.open_skills_master import api_sync_dag
from api_sync.v1.models import JobMaster,\
    SkillMaster,\
    SkillImportance,\
    GeoTitleCount,\
    TitleCount
import os
import logging

DEFAULT_DATE = datetime(2013, 5, 1)
configuration.load_test_config()


def test_dag():
    with testing.postgresql.Postgresql() as postgresql:
        with patch.dict(os.environ, {
            'API_V1_DB_URL': postgresql.url(),
            'OUTPUT_FOLDER': 'tests/api_sync_v1/input'
        }):
            configuration.load_test_config()
            # the scheduler messages, which will show up if something
            # happens to screw up execution, are INFO level so save us
            # some headaches but switching to that loglevel here
            logging.basicConfig(level=logging.INFO)
            bag = models.DagBag()
            dag = bag.get_dag(dag_id='open_skills_master.api_v1_sync')
            # expire old DAG runs, otherwise the max of 16 will automatically get scheduled
            dag.dagrun_timeout = 1
            dag.clear()
            job = BackfillJob(
                dag=dag,
                start_date=DEFAULT_DATE,
                end_date=DEFAULT_DATE,
            )
            job.run()
            engine = create_engine(postgresql.url())
            session = sessionmaker(engine)()
            num_jobs = session.query(JobMaster).count()
            assert num_jobs > 1
            num_skills = session.query(SkillMaster).count()
            assert num_skills > 1
            num_importances = session.query(SkillImportance).count()
            assert num_importances > 1
            assert session.query(GeoTitleCount).count() > 1
            assert session.query(TitleCount).count() > 1

            # make sure non-temporal data doesn't
            # load twice for a different quarter
            new_date = datetime(2014, 5, 1)
            dag.clear(start_date=new_date, end_date=new_date)
            dag.run(start_date=new_date, end_date=new_date, local=True)
            assert session.query(JobMaster).count() == num_jobs
            assert session.query(SkillMaster).count() == num_skills
            assert session.query(SkillImportance).count() == num_importances
