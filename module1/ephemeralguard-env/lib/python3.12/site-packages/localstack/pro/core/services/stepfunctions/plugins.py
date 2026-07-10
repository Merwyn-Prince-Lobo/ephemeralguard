from localstack.packages import Package
from localstack.pro.core.packages.core import pro_package
@pro_package(name='stepfunctions')
def stepfunctions_package():from localstack.pro.core.services.stepfunctions.packages import stepfunctions_pro_package as A;return A