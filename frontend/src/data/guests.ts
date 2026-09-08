import { Guest } from "@/lib/types";

function avatar(seed: string) {
  return `https://api.dicebear.com/9.x/notionists/svg?seed=${encodeURIComponent(seed)}&backgroundColor=eef2fa,fdecec`;
}

export const guests: Guest[] = [
  { id: "G-001", name: "Aarav Mehta", email: "aarav.mehta@example.com", phone: "+91 98200 11223", avatar: avatar("Aarav Mehta"), location: "Mumbai, IN", totalBookings: 6, totalSpent: 84250, joinedAt: "2024-02-14", status: "active" },
  { id: "G-002", name: "Isha Kapoor", email: "isha.kapoor@example.com", phone: "+91 98110 22334", avatar: avatar("Isha Kapoor"), location: "Delhi, IN", totalBookings: 4, totalSpent: 52300, joinedAt: "2024-05-02", status: "active" },
  { id: "G-003", name: "Rohan Verma", email: "rohan.verma@example.com", phone: "+91 90040 33445", avatar: avatar("Rohan Verma"), location: "Bengaluru, IN", totalBookings: 2, totalSpent: 21980, joinedAt: "2024-08-19", status: "active" },
  { id: "G-004", name: "Sneha Iyer", email: "sneha.iyer@example.com", phone: "+91 99870 44556", avatar: avatar("Sneha Iyer"), location: "Chennai, IN", totalBookings: 8, totalSpent: 132400, joinedAt: "2023-11-30", status: "active" },
  { id: "G-005", name: "Vikram Singh", email: "vikram.singh@example.com", phone: "+91 96500 55667", avatar: avatar("Vikram Singh"), location: "Jaipur, IN", totalBookings: 1, totalSpent: 8999, joinedAt: "2025-01-11", status: "inactive" },
  { id: "G-006", name: "Ananya Rao", email: "ananya.rao@example.com", phone: "+91 93430 66778", avatar: avatar("Ananya Rao"), location: "Pune, IN", totalBookings: 5, totalSpent: 67800, joinedAt: "2024-03-27", status: "active" },
  { id: "G-007", name: "Karan Malhotra", email: "karan.malhotra@example.com", phone: "+91 91234 77889", avatar: avatar("Karan Malhotra"), location: "Chandigarh, IN", totalBookings: 3, totalSpent: 39900, joinedAt: "2024-06-08", status: "active" },
  { id: "G-008", name: "Priya Nair", email: "priya.nair@example.com", phone: "+91 89020 88990", avatar: avatar("Priya Nair"), location: "Kochi, IN", totalBookings: 7, totalSpent: 98650, joinedAt: "2023-09-15", status: "active" },
  { id: "G-009", name: "Aditya Kulkarni", email: "aditya.kulkarni@example.com", phone: "+91 88770 99001", avatar: avatar("Aditya Kulkarni"), location: "Nagpur, IN", totalBookings: 1, totalSpent: 6499, joinedAt: "2025-04-02", status: "inactive" },
  { id: "G-010", name: "Meera Pillai", email: "meera.pillai@example.com", phone: "+91 87660 10012", avatar: avatar("Meera Pillai"), location: "Hyderabad, IN", totalBookings: 4, totalSpent: 58200, joinedAt: "2024-01-22", status: "active" },
];
