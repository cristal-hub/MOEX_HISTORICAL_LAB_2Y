from importlib.metadata import version
from t_tech.invest import Client, CandleInterval

print("SDK VERSION:", version("t-tech-investments"))
print("IMPORTS: OK")
print("\nAVAILABLE CANDLE INTERVALS:")

for name, value in CandleInterval.__members__.items():
    print(name, "=", value)

