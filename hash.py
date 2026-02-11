import bcrypt
print(bcrypt.hashpw(b'Fr3ck3lS', bcrypt.gensalt()).decode())