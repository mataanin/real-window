from .loopnet import LoopNet
from .crexi import Crexi
from .commercialcafe import CommercialCafe
from .brevitas import Brevitas
from .zillow import Zillow
from .realtor import Realtor
from .redfin import Redfin
from .homes import HomesCom
from .ctjudicial import CTJudicial
from .nepcg import NEPCG

ADAPTERS = {a.name: a for a in (LoopNet(), Crexi(), CommercialCafe(), Brevitas(), Zillow(), Realtor(), Redfin(),
                                HomesCom(), CTJudicial(), NEPCG())}
