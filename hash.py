import bcrypt
print(bcrypt.hashpw(b'BondsRulezGriffey$uck$', bcrypt.gensalt()).decode())