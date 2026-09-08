const ids = {
  hotelBed: "1566073771259-6a8506099945",
  hotelBedroom: "1582719478250-c89cae4dc85b",
  hotelSuite: "1590490360182-c33d57733427",
  hotelRoom: "1611892440504-42a792e24d32",
  hotelLounge: "1520250497591-112f2f40a3f4",
  hotelBedroom2: "1611048267451-e6ed903d4a38",
  hotelRoom2: "1560185127-6ed189bf02f4",
  hotelHallway: "1618773928121-c32242e63f39",
  resortPool: "1551882547-ff40c63fe5fa",
  villaPool: "1571896349842-33c89424de2d",
  houseLiving: "1512917774080-9991f1c4c750",
  villaInterior: "1600596542815-ffad4c1539a9",
  villaExterior: "1600585154340-be6161a56a0c",
  houseModern: "1568605114967-8130f3a36994",
  houseExterior: "1449844908441-8829872d2607",
  coworkingDesk: "1497366811353-6870744d04b2",
  coworkingLounge: "1522071820081-009f0129c71c",
  hostelDorm: "1555854877-bab0e564b8d5",
  hostelStudy: "1517248135467-4c7edcad34c4",
} as const;

export function unsplash(key: keyof typeof ids, w = 1200, q = 80) {
  return `https://images.unsplash.com/photo-${ids[key]}?w=${w}&q=${q}&auto=format&fit=crop`;
}
