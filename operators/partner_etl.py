from airflow.models import BaseOperator
from airflow.hooks import S3Hook
from airflow.utils.decorators import apply_defaults
from skills_ml.datasets.onet_cache import OnetCache
from skills_ml.algorithms.aggregators.dataset_transform import \
    DatasetStatsCounter, DatasetStatsAggregator, GlobalStatsAggregator
from skills_utils.iteration import Batch
from skills_utils.time import datetime_to_quarter
import tempfile
import boto
import uuid
import json
import logging
from config import config


class PartnerETLOperator(BaseOperator):
    @apply_defaults
    def __init__(
            self,
            transformer_class,
            output_bucket,
            output_prefix,
            partner_id,
            passthrough_kwargs,
            postings_per_file=10000,
            *args, **kwargs):
        super(PartnerETLOperator, self).__init__(*args, **kwargs)
        self.transformer_class = transformer_class
        self.output_bucket = output_bucket
        self.output_prefix = output_prefix
        self.partner_id = partner_id
        self.postings_per_file = postings_per_file
        self.passthrough_kwargs = passthrough_kwargs

    def clear_old_postings(self, connection, quarter):
        logging.info('Clearing out old postings')
        bucket = connection.get_bucket(self.output_bucket)
        partner_prefix = '{}/{}/{}_'.format(
            self.output_prefix,
            quarter,
            self.partner_id
        )
        keylist = list(bucket.list(prefix=partner_prefix, delimiter=''))
        logging.info('Found %s old postings', len(keylist))
        for key in keylist:
            key.delete()
        logging.info('Done deleting postings')

    def execute(self, context):
        conn = S3Hook().get_conn()
        quarter = datetime_to_quarter(context['execution_date'])
        stats_counter = DatasetStatsCounter(
            quarter=quarter,
            dataset_id=self.partner_id
        )
        transformer = self.transformer_class(
            s3_conn=conn,
            partner_id=self.partner_id,
            onet_cache=OnetCache(
                s3_conn=conn,
                cache_dir=config['onet']['cache_dir'],
                s3_path=config['onet']['s3_path'],
            ),
            **self.passthrough_kwargs
        )
        self.clear_old_postings(conn, quarter)
        for batch in Batch(
            transformer.postings(quarter, stats_counter),
            self.postings_per_file
        ):
            logging.info('Processing new batch')
            with tempfile.TemporaryFile(mode='w+') as f:
                for posting in batch:
                    f.write(json.dumps(posting))
                    f.write('\n')

                logging.debug('New batch written, commencing upload')
                bucket = conn.get_bucket(self.output_bucket)
                key = boto.s3.key.Key(
                    bucket=bucket,
                    name='{}/{}/{}_{}'.format(self.output_prefix, quarter,
                                              self.partner_id, uuid.uuid4())
                )
                f.seek(0)
                key.set_contents_from_string(f.read())
                logging.debug('Batch upload complete')
        stats_counter.save(
            s3_conn=conn,
            s3_prefix=config['partner_stats']['s3_path']
        )


class PartnerStatsAggregateOperator(BaseOperator):
    @apply_defaults
    def __init__(self, partner_id, *args, **kwargs):
        super(PartnerStatsAggregateOperator, self).__init__(*args, **kwargs)
        self.partner_id = partner_id

    def execute(self, context):
        conn = S3Hook().get_conn()
        stats_aggregator = DatasetStatsAggregator(
            dataset_id=self.partner_id,
            s3_conn=conn
        )
        stats_aggregator.run(config['partner_stats']['s3_path'])


class GlobalStatsAggregateOperator(BaseOperator):
    def execute(self, context):
        conn = S3Hook().get_conn()
        stats_aggregator = GlobalStatsAggregator(s3_conn=conn)
        stats_aggregator.run(config['partner_stats']['s3_path'])
