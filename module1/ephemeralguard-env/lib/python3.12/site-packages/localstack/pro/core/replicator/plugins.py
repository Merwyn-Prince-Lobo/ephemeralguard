import logging
from localstack.pro.core.config import ENABLE_REPLICATOR_API
from localstack.runtime import hooks
from localstack.services.internal import get_internal_apis
LOG=logging.getLogger(__name__)
@hooks.on_infra_start()
def register_replicator_api():
	if ENABLE_REPLICATOR_API:from localstack.pro.core.replicator.api import ReplicatorApi as A;LOG.info('Replicator API enabled');get_internal_apis().add(A())