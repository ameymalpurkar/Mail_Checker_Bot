#!/usr/bin/env python3
"""
Phishing Detection API Server with FastAPI

This script implements a REST API using FastAPI to detect phishing indicators 
in text content. It exposes three endpoints for phishing analysis.
"""

import sys
import re
import os
from urllib.parse import urlparse
from typing import Dict, Any, Optional

# Check if we can import the necessary libraries
try:
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel
    import uvicorn
    import google.generativeai as genai
    from google.generativeai.types import HarmCategory, HarmBlockThreshold
    print("✅ FastAPI and dependencies imported successfully", file=sys.stderr)
except ImportError as e:
    print(f"❌ Failed to import necessary libraries: {e}", file=sys.stderr)
    print("Hint: Install required packages with: pip install fastapi uvicorn google-generativeai", file=sys.stderr)
    sys.exit(1)

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("⚠️ python-dotenv not installed. Using environment variables directly.", file=sys.stderr)

# Configure Gemini API
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
if not GEMINI_API_KEY:
    print("⚠️ Warning: GEMINI_API_KEY not found in environment variables or .env file", file=sys.stderr)
    print("Please set your Gemini API key using one of these methods:", file=sys.stderr)
    print("1. Create a .env file with: GEMINI_API_KEY=your-api-key-here", file=sys.stderr)
    print("2. Set environment variable: $env:GEMINI_API_KEY='your-api-key-here'", file=sys.stderr)
    sys.exit(1)
else:
    genai.configure(api_key=GEMINI_API_KEY)
    
    # Configure the model
    generation_config = {
        "temperature": 0.7,
        "top_p": 0.8,
        "top_k": 40,
        "max_output_tokens": 1024,
    }

    safety_settings = [
        {
            "category": "HARM_CATEGORY_HARASSMENT",
            "threshold": "BLOCK_MEDIUM_AND_ABOVE"
        },
        {
            "category": "HARM_CATEGORY_HATE_SPEECH",
            "threshold": "BLOCK_MEDIUM_AND_ABOVE"
        },
        {
            "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
            "threshold": "BLOCK_MEDIUM_AND_ABOVE"
        },
        {
            "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
            "threshold": "BLOCK_MEDIUM_AND_ABOVE"
        }
    ]


# --- Pydantic Models for Request/Response ---
class TextAnalysisRequest(BaseModel):
    text: str

class AnalysisResponse(BaseModel):
    result: str
    risk_score: float = None
    risk_level: str = None
    suspicious_elements: dict = {
        'urls': [],
        'urgent_phrases': [],
        'credential_phrases': []
    }


# --- Create FastAPI app ---
app = FastAPI(
    title="Phishing Detector API",
    description="API for detecting phishing indicators in text content",
    version="2.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8000",
        "http://localhost:3000",
        "https://mail.google.com",
        "chrome-extension://*",  # Allow Chrome extensions
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Core Phishing Detection Logic ---
def levenshtein_distance(s1: str, s2: str) -> int:
    """Calculate the Levenshtein distance between two strings."""
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)

    if len(s2) == 0:
        return len(s1)

    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]

def extract_features(text: str) -> Dict[str, bool]:
    """
    Extracts various features from the input text that can indicate phishing attempts.

    Args:
        text (str): The text content (e.g., email body) to analyze.

    Returns:
        dict: A dictionary where keys are feature names and values are booleans
              indicating if the feature is present (True) or not (False).
    """
    features = {
        'has_urgency': False,
        'has_suspicious_links': False,
        'has_credential_request': False,
        'has_suspicious_sender': False,
        'has_poor_formatting': False,
        'has_typosquatting': False
    }
    
    # List of common legitimate domains to check against
    legitimate_domains = [
        'google.com', 'facebook.com', 'amazon.com', 'microsoft.com', 'apple.com',
        'netflix.com', 'paypal.com', 'twitter.com', 'instagram.com', 'linkedin.com',
        'youtube.com', 'gmail.com', 'yahoo.com', 'outlook.com', 'github.com',
        'dropbox.com', 'spotify.com', 'twitch.tv', 'reddit.com', 'wikipedia.org'
    ]
    
    # Convert text to lowercase for case-insensitive matching
    text_lower = text.lower()

    # 1. Check for urgency-related keywords
    urgency_words = [
        'urgent', 'immediate', 'action required', 'account suspended',
        'security alert', 'unauthorized', 'verify your account',
        'expire', 'limited time', 'click now'
    ]
    features['has_urgency'] = any(word in text_lower for word in urgency_words)

    # 2. Check for suspicious links (TLDs, IP addresses, uncommon ports, typosquatting)
    urls = re.findall(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', text)
    
    suspicious_tlds = ['.tk', '.ml', '.ga', '.cf', '.gq', '.xyz', '.online', '.site', '.top', '.bid']

    for url in urls:
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            
            # Remove port number if present
            if ':' in domain:
                domain = domain.split(':')[0]

            # Check for typosquatting
            for legitimate_domain in legitimate_domains:
                # Strip 'www.' if present for comparison
                clean_domain = domain.replace('www.', '')
                clean_legitimate = legitimate_domain.replace('www.', '')
                
                # Calculate Levenshtein distance
                distance = levenshtein_distance(clean_domain, clean_legitimate)
                
                # If domain is similar but not identical to a legitimate domain
                if 0 < distance <= 2 and clean_domain != clean_legitimate:
                    features['has_typosquatting'] = True
                    features['has_suspicious_links'] = True
                    break

            # Check if domain ends with a suspicious TLD
            if any(domain.endswith(tld) for tld in suspicious_tlds):
                features['has_suspicious_links'] = True
                break

            # Check for IP addresses in the hostname
            if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", domain):
                features['has_suspicious_links'] = True
                break

            # Check for uncommon ports
            if parsed.port is not None and parsed.port not in [80, 443]:
                features['has_suspicious_links'] = True
                break

        except Exception:
            features['has_suspicious_links'] = True
            break
    
    # 3. Check for credential request keywords
    credential_words = [
        'password', 'login', 'credential', 'verify', 'bank account',
        'credit card', 'social security', 'ssn', 'account details',
        'update payment', 'confirm identity', 'reset password', 'security code'
    ]
    features['has_credential_request'] = any(word in text_lower for word in credential_words)

    # 4. Check for suspicious sender patterns
    suspicious_patterns = [
        r'@.*\.(tk|ml|ga|cf|gq|xyz|online|site|top|bid)$',
        r'support.*@(?!yourcompany\.com)',
        r'security.*@(?!yourcompany\.com)',
        r'admin.*@(?!yourcompany\.com)',
        r'noreply.*@(?!yourcompany\.com)'
    ]
    features['has_suspicious_sender'] = any(re.search(pattern, text_lower) for pattern in suspicious_patterns)

    # 5. Check for poor formatting indicators
    features['has_poor_formatting'] = (
        text.count('!') > 3 or
        text.count('$') > 2 or
        len(re.findall(r'[A-Z]{4,}', text)) > 2 or
        ('click here' in text_lower and not urls) or
        ('kindly' in text_lower and text_lower.count('kindly') > 1)
    )
    
    return features


def calculate_risk_score(features: Dict[str, bool]) -> float:
    """Calculate weighted risk score based on detected features."""
    feature_weights = {
        'has_urgency': 0.15,
        'has_suspicious_links': 0.25,
        'has_credential_request': 0.20,
        'has_suspicious_sender': 0.15,
        'has_poor_formatting': 0.10,
        'has_typosquatting': 0.15  # High weight for typosquatting as it's a strong phishing indicator
    }
    
    return sum(feature_weights[feature] for feature, present in features.items() if present)


def get_risk_level(risk_score: float) -> str:
    """Determine risk level based on risk score."""
    if risk_score >= 0.7:
        return "HIGH RISK - This message shows strong indicators of being a phishing attempt."
    elif risk_score >= 0.4:
        return "MEDIUM RISK - This message shows some suspicious characteristics."
    else:
        return "LOW RISK - This message shows few or no suspicious characteristics."


# --- Gemini AI Analysis ---
async def analyze_with_gemini(text: str) -> Dict[str, Any]:
    """
    Comprehensive AI-powered phishing email analysis using Gemini.
    
    Args:
        text (str): The email text to analyze
    
    Returns:
        Dict containing detailed AI analysis results
    """
    try:
        model = genai.GenerativeModel('gemini-pro',
                                    generation_config=generation_config,
                                    safety_settings=safety_settings)
        
        prompt = f"""
        You are an expert email security analyst. Perform a comprehensive analysis of this email/text for phishing attempts.
        
        Analyze the following aspects in detail:

        1. URL Analysis:
           - Identify any suspicious URLs
           - Check for typosquatting (e.g., 'paypa1.com' instead of 'paypal.com')
           - Look for IP addresses instead of domain names
           - Detect unusual TLDs or suspicious domains

        2. Social Engineering Tactics:
           - Identify pressure tactics or urgency
           - Look for emotional manipulation
           - Check for authority impersonation
           - Detect false promises or threats

        3. Content Analysis:
           - Analyze grammar and spelling quality
           - Check for inconsistent formatting
           - Identify generic greetings
           - Look for excessive punctuation
           - Detect copied logos or templates

        4. Sensitive Information Requests:
           - Identify requests for passwords
           - Look for financial information requests
           - Detect requests for personal data
           - Check for unusual verification requests

        5. Technical Indicators:
           - Analyze email headers (if present)
           - Check sender address authenticity
           - Look for mismatched display names
           - Identify suspicious attachments

        6. Behavioral Manipulation:
           - Detect urgency creation
           - Identify threat tactics
           - Look for unusual requests
           - Check for out-of-character communication

        Text to analyze:
        {text}

        Respond in this exact JSON format:
        {{
            "risk_level": "high/medium/low",
            "confidence_score": 0.0-1.0,
            "suspicious_elements": {{
                "urls": ["list of suspicious URLs with explanation"],
                "urgent_phrases": ["list of urgent/pressure phrases found"],
                "credential_phrases": ["list of phrases requesting sensitive info"],
                "impersonation_tactics": ["list of impersonation attempts found"],
                "technical_issues": ["list of technical red flags"],
                "manipulation_tactics": ["list of manipulation tactics used"]
            }},
            "security_recommendations": ["list of specific actions user should take"],
            "detailed_analysis": "comprehensive explanation of all findings",
            "safe_to_interact": false/true,
            "primary_threat_indicators": ["list of most concerning elements"],
            "suggested_actions": ["specific steps to take if user has already interacted"]
        }}
        """

        response = await model.generate_content_async(prompt)
        return eval(response.text)  # Convert string response to dict
        
    except Exception as e:
        print(f"Gemini AI analysis failed: {str(e)}", file=sys.stderr)
        return {
            "risk_level": "unknown",
            "confidence_score": 0.0,
            "suspicious_elements": {"urls": [], "urgent_phrases": [], "credential_phrases": []},
            "ai_explanation": f"AI analysis failed: {str(e)}"
        }

# --- API Endpoints ---

@app.get("/")
async def root():
    """Root endpoint with API information."""
    return {
        "message": "Phishing Detection API",
        "version": "1.0.0",
        "endpoints": {
            "analyze_text": "POST /analyze_text - Comprehensive phishing analysis",
            "quick_check": "POST /quick_check - Quick phishing assessment",
            "analyze_links": "POST /analyze_links - Link-specific analysis"
        }
    }


@app.post("/analyze_text", response_model=AnalysisResponse)
async def analyze_text(request: TextAnalysisRequest):
    """
    Performs comprehensive AI-powered phishing analysis using Gemini.
    
    Returns a detailed security analysis report.
    """
    try:
        # Get AI analysis
        ai_analysis = await analyze_with_gemini(request.text)
        
        # Create comprehensive report
        report = ["� Advanced Phishing Security Analysis �\n"]
        
        # Risk Assessment
        report.append(f"Risk Level: {ai_analysis['risk_level'].upper()}")
        report.append(f"Confidence Score: {ai_analysis['confidence_score']:.2f}/1.00")
        report.append(f"Safe to Interact: {'✅ Yes' if ai_analysis['safe_to_interact'] else '❌ No'}")
        
        # Primary Threats
        if ai_analysis['primary_threat_indicators']:
            report.append("\n🚨 Primary Threat Indicators:")
            for threat in ai_analysis['primary_threat_indicators']:
                report.append(f"• {threat}")
        
        # Suspicious URLs
        if ai_analysis['suspicious_elements']['urls']:
            report.append("\n🔗 Suspicious URLs Detected:")
            for url in ai_analysis['suspicious_elements']['urls']:
                report.append(f"• {url}")
        
        # Social Engineering Tactics
        if ai_analysis['suspicious_elements']['manipulation_tactics']:
            report.append("\n🎭 Manipulation Tactics Identified:")
            for tactic in ai_analysis['suspicious_elements']['manipulation_tactics']:
                report.append(f"• {tactic}")
        
        # Technical Issues
        if ai_analysis['suspicious_elements']['technical_issues']:
            report.append("\n⚠️ Technical Red Flags:")
            for issue in ai_analysis['suspicious_elements']['technical_issues']:
                report.append(f"• {issue}")
        
        # Urgent/Pressure Phrases
        if ai_analysis['suspicious_elements']['urgent_phrases']:
            report.append("\n⚡ Pressure Tactics Found:")
            for phrase in ai_analysis['suspicious_elements']['urgent_phrases']:
                report.append(f"• {phrase}")
        
        # Credential/Sensitive Info Requests
        if ai_analysis['suspicious_elements']['credential_phrases']:
            report.append("\n🔑 Requests for Sensitive Information:")
            for phrase in ai_analysis['suspicious_elements']['credential_phrases']:
                report.append(f"• {phrase}")
        
        # Impersonation Attempts
        if ai_analysis['suspicious_elements']['impersonation_tactics']:
            report.append("\n👤 Impersonation Attempts:")
            for tactic in ai_analysis['suspicious_elements']['impersonation_tactics']:
                report.append(f"• {tactic}")
        
        # Detailed Analysis
        report.append("\n📝 Detailed Analysis:")
        report.append(ai_analysis['detailed_analysis'])
        
        # Security Recommendations
        report.append("\n✅ Security Recommendations:")
        for rec in ai_analysis['security_recommendations']:
            report.append(f"• {rec}")
        
        # Action Steps if Already Interacted
        if not ai_analysis['safe_to_interact']:
            report.append("\n🚨 If You've Already Interacted:")
            for action in ai_analysis['suggested_actions']:
                report.append(f"• {action}")
                
        # Use the confidence score as the risk score
        risk_score = ai_analysis['confidence_score']
        
        return AnalysisResponse(
            result="\n".join(report),
            risk_score=risk_score,
            risk_level=ai_analysis['risk_level'],
            suspicious_elements={
                'urls': ai_analysis['suspicious_elements']['urls'],
                'urgent_phrases': ai_analysis['suspicious_elements']['urgent_phrases'],
                'credential_phrases': ai_analysis['suspicious_elements']['credential_phrases']
            }
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")


@app.post("/quick_check", response_model=AnalysisResponse)
async def quick_check(request: TextAnalysisRequest):
    """
    Provides a quick assessment of phishing likelihood.
    
    Returns a brief summary of the risk level.
    """
    try:
        features = extract_features(request.text)
        num_suspicious_features = sum(1 for present in features.values() if present)
        
        if num_suspicious_features >= 3:
            result = "🚨 High likelihood of phishing! Exercise extreme caution and do not interact."
        elif num_suspicious_features >= 1:
            result = "⚠️ Some suspicious elements detected. Review carefully before proceeding."
        else:
            result = "✅ Low risk - few or no suspicious elements detected."
            
        return AnalysisResponse(result=result)
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Quick check failed: {str(e)}")


@app.post("/analyze_links", response_model=AnalysisResponse)
async def analyze_links(request: TextAnalysisRequest):
    """
    Analyzes all links in the text for phishing risk.
    
    Returns a detailed report on each identified link.
    """
    try:
        # Find all URLs in the text
        urls = re.findall(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', request.text)
        
        if not urls:
            return AnalysisResponse(result="No links found in the provided text.")
        
        suspicious_domains = ['.tk', '.ml', '.ga', '.cf', '.gq', '.xyz', '.online', '.site', '.top', '.bid']
        report = ["🔗 Link Analysis Report 🔗\n"]
        
        for url in urls:
            try:
                parsed = urlparse(url)
                domain = parsed.netloc
                
                is_suspicious_domain = any(domain.endswith(susp) or susp in domain for susp in suspicious_domains)
                is_ip_address_domain = re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", domain)
                has_uncommon_port = parsed.port is not None and parsed.port not in [80, 443]
                
                if is_suspicious_domain or is_ip_address_domain or has_uncommon_port:
                    report.append(f"🚨 Suspicious link: {url}")
                else:
                    report.append(f"✅ Safe-looking link: {url}")
            except Exception as e:
                report.append(f"⚠️ Could not analyze link: {url} (Error: {e})")
        
        return AnalysisResponse(result="\n".join(report))
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Link analysis failed: {str(e)}")


# --- Server Execution ---
if __name__ == "__main__":
    SERVER_PORT = 8000
    
    print(f"🚀 Starting Phishing Detection API server on http://localhost:{SERVER_PORT}...", file=sys.stderr)
    print("📡 Available endpoints:", file=sys.stderr)
    print("   POST /analyze_text - Comprehensive analysis", file=sys.stderr)
    print("   POST /quick_check - Quick assessment", file=sys.stderr)
    print("   POST /analyze_links - Link analysis", file=sys.stderr)
    print("💡 To stop the server, press Ctrl+C", file=sys.stderr)
    
    try:
        uvicorn.run(
            "phishing_detection:app",
            host="0.0.0.0",
            port=SERVER_PORT,
            reload=True
        )
    except KeyboardInterrupt:
        print("\n👋 Server stopped by user", file=sys.stderr)
    except Exception as e:
        print(f"💥 Server error: {e}", file=sys.stderr)
        sys.exit(1)