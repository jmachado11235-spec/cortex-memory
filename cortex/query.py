"""Small, inspectable query cleanup; no model/API calls or hidden translation."""
import re
import unicodedata

STOPWORDS = set('''a an and are as at be been but by can could did do does for from had has
have how i if in into is it its just me my of on or our please so than that the their
them then there theres these they this those to today too us was we were what when where
which who why will with would you your yet made make changes change think recommend
analyze analyse improve setup current anything something know want ok okay thanks thank
continue proceed resume yes sure go ahead seguir continua continuar
de del la las el los en un una unos unas por para que como con es son se su sus al y
mi mis quiero saber puedes favor hoy hay hacer cambios sistema actual
'''.split())

# Conservative, inspectable bilingual vocabulary. Each concept scores once.
CONCEPTS = (
    ('payment','payments','pago','pagos'), ('receipt','receipts','recibo','recibos'),
    ('loan','loans','prestamo','prestamos'), ('interest','interes','intereses'),
    ('inventory','inventario','inventarios'), ('sweep','barrido'),
    ('nightly','nocturno','nocturna'), ('frozen','congelado','congelada'),
    ('trigger','triggers','disparador','disparadores'), ('orphan','huerfano','huerfana'),
    ('batch','lote'), ('lateness','atraso'), ('rate','rates','tasa','tasas'),
    ('hibernation','hibernacion','hiberfil'), ('backup','backups','respaldo','respaldos'),
    ('memory','memoria'), ('commission','comision','comisiones'),
)

def variants(term):
    term=fold(term)
    return next((group for group in CONCEPTS if term in group),(term,))

def fold(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', value.lower())
                   if not unicodedata.combining(c))

def terms(values):
    tokens = re.findall(r'[\w]+(?:[-.][\w]+)*', fold(' '.join(values)))
    return list(dict.fromkeys(t for t in tokens if len(t)>1 and t not in STOPWORDS))

def automatic_query(prompt):
    """Route only explicit topics. Ambiguous follow-ups return no recall."""
    cleaned = terms([prompt])
    if any(t in cleaned for t in ('cortex','memory','memoria','vault')):
        return 'cortex ' + ' '.join(t for t in cleaned if t not in ('cortex','memory','memoria','vault')), 'meta'
    return ' '.join(cleaned[:18]), None
